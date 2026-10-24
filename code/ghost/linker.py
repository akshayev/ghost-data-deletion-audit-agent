"""Which resources belong to each request's subject. No state, policy or planning.

Channels, in order:
  1. KEY: an observation (any capture time) carries a strong key of the subject.
  2. CATALOG: a LINK_RESOURCE_IDENTITY activity names a subject email/user_id. Its alias
     (statement number, file name, user ref) is searched in every evidence file and each
     hit becomes a synthetic observation (copy="alias") that later steps can cite.
  3. WORKER_WINDOW: deletion_worker activities on resources nobody owns are assigned to
     cases whose reported_at puts them inside a window learned from KEY-linked resources.
     Assignment is global over both splits (cases compete for the same events).
  4. STALE filter: drop resources only seen before the request with no worker event in
     the case's windows.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache

from ghost import loaders, paths
from ghost.identity import IdentityResolver
from ghost.loaders import Activity, DeletionRequest
from ghost.observation import FileFacts, Observation
from ghost.systems import RESOURCE_ID_RE, canonical

WORKER = "deletion_worker"
LINK_EVENT = "LINK_RESOURCE_IDENTITY"
TEMPLATE_CHARS = 40
FAMILY_CHARS = 30
LEARN_RANGE_HOURS = 6.0
WINDOW_PAD_HOURS = 0.1
DEFAULT_WINDOW = (-1.5, 2.5)
SUBMITTED = "submitted primary profile"
STATUS_RESULT = {"COMPLETE": "SUCCESS", "NOT_STARTED": "FAILED", "PARTIALLY_COMPLETE": "PARTIAL"}

_CATALOG_RES = (
    re.compile(r"Data catalog linked (\S+) \(([^)]+)\) to (\S+@\S+?) and (user_\d+)\."),
    re.compile(
        r"Attachment catalog confirmed (\S+) \(([^)]+)\) belongs only to (\S+@\S+?) and "
        r"(user_\d+);"
    ),
)
_USER_RE = re.compile(r"^user_\d+$")


# ---------- pure helpers ----------


def template(a: Activity) -> str:
    return f"{a.event_type}|{RESOURCE_ID_RE.sub('X', a.details)[:TEMPLATE_CHARS]}"


def family(a: Activity) -> str:
    text = " ".join(RESOURCE_ID_RE.sub("X", a.details).lower().split())
    return text[:FAMILY_CHARS]


def parse_catalog(details: str) -> tuple[str, str, str, str] | None:
    """(resource_id, alias, email, user_id) from a LINK_RESOURCE_IDENTITY details text."""
    for pattern in _CATALOG_RES:
        m = pattern.search(details)
        if m and RESOURCE_ID_RE.fullmatch(m.group(1)):
            rid, alias, email, uid = m.groups()
            return rid, alias, email.lower(), uid
    return None


def rtype(resource_id: str) -> str:
    m = RESOURCE_ID_RE.fullmatch(resource_id)
    return m.group(1) if m else ""


def case_num(case_id: str) -> int:
    return int(case_id.rsplit("_", 1)[1])


def _natural(resource_id: str) -> tuple[str, int]:
    prefix, _, num = resource_id.rpartition("_")
    return prefix, int(num) if num.isdigit() else -1


def _hours(a: Activity, at: datetime) -> float:
    return (a.event_time - at).total_seconds() / 3600


def _alias_re(alias: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])")


@cache
def file_texts() -> dict[str, str]:
    """file_id -> searchable text of the whole file (PDF text, screenshot transcription)."""
    from ghost.parsers.images import load_extraction
    from ghost.parsers.pdf import pdf_text

    out = {}
    for ef in loaders.load_evidence_files():
        path = paths.DATASET / ef.file_path
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            out[ef.file_id] = pdf_text(path)
        elif suffix == ".png":
            result = load_extraction(path, use_llm=False)
            out[ef.file_id] = result.text if result else ""
        else:
            out[ef.file_id] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _is_identity_key(key: str) -> bool:
    """Keys that name a person when unknown (emails, user ids). Unknown hashes are not
    treated as identities: e.g. audit integrity seals are 64-hex but nobody's email hash."""
    return "@" in key or bool(_USER_RE.match(key))


# ---------- output ----------


@dataclass(frozen=True)
class LinkedResource:
    resource_id: str
    system_id: str
    via: tuple  # ("key", key, file_id) | ("catalog", activity_id) | ("worker_window", aid, tie)
    activity_ids: tuple[str, ...]
    observations: tuple[Observation, ...]
    flags: dict = field(default_factory=dict)


@dataclass
class _Catalog:
    activity_id: str
    resource_id: str
    alias: str
    email: str
    user_id: str


# ---------- linker ----------


class Linker:
    def __init__(
        self,
        requests: Iterable[DeletionRequest],
        observations: Iterable[Observation],
        facts: Iterable[FileFacts],
        activities: Iterable[Activity],
        resolver: IdentityResolver,
        texts: dict[str, str],
        captured: dict[str, datetime],
        non_deletion: frozenset[str] = frozenset(),
    ) -> None:
        self.requests = {r.case_id: r for r in requests}
        self.case_ids = sorted(self.requests, key=case_num)
        self.resolver = resolver
        self.non_deletion = non_deletion
        self.key_index = resolver.all_keys_index()  # strong key -> user_id
        self.subjects = {c: resolver.resolve(r.email) for c, r in self.requests.items()}

        self.activities = sorted(activities, key=lambda a: (a.event_time, a.activity_id))
        self.activity_by_id = {a.activity_id: a for a in self.activities}
        self.acts_by_resource: dict[str, list[Activity]] = defaultdict(list)
        for a in self.activities:
            self.acts_by_resource[a.resource_id].append(a)

        self.obs_by_resource: dict[str, list[Observation]] = defaultdict(list)
        self.obs_by_key: dict[str, list[Observation]] = defaultdict(list)
        for o in observations:
            self.obs_by_resource[o.resource_id].append(o)
            for k in o.link_keys:
                if not k.startswith("name:"):
                    self.obs_by_key[k].append(o)

        self.catalog = self._parse_catalog()
        self._add_alias_observations(facts, texts, captured)
        self.owners = self._owners()

        self.key_links = {c: self._key_links(c) for c in self.case_ids}
        self.catalog_links = {c: self._catalog_links(c) for c in self.case_ids}
        self.windows = self._learn_windows()
        self.worker_links, self.tie_breaks = self._assign_workers()
        self._cache: dict[str, list[LinkedResource]] = {}

    @classmethod
    def from_dataset(cls, non_deletion: frozenset[str] = frozenset()) -> "Linker":
        from ghost.parsers.registry import parse_all

        observations, _, facts = parse_all()
        return cls(
            requests=loaders.load_requests("sample") + loaders.load_requests("full"),
            observations=observations,
            facts=facts,
            activities=loaders.load_activities(),
            resolver=IdentityResolver(loaders.load_identities(), loaders.load_audit_changes()),
            texts=file_texts(),
            captured={ef.file_id: ef.captured_at for ef in loaders.load_evidence_files()},
            non_deletion=non_deletion,
        )

    # ----- catalog + alias bridge -----

    def _parse_catalog(self) -> list[_Catalog]:
        out = []
        for a in self.activities:
            if a.event_type != LINK_EVENT:
                continue
            parsed = parse_catalog(a.details)
            if parsed:
                out.append(_Catalog(a.activity_id, *parsed))
        return out

    def _add_alias_observations(self, facts, texts, captured) -> None:
        fact_text = defaultdict(list)
        for f in facts:
            values = [*f.user_ids, *f.order_refs, *f.emails, *f.ticket_refs, *f.file_names]
            fact_text[f.file_id].extend(values)
        # alias -> files where it is already a link key of some resource's observation:
        # there the mention belongs to that resource, not to the catalogued one.
        keyed_in: dict[str, set[str]] = defaultdict(set)
        for obs in self.obs_by_resource.values():
            for o in obs:
                for k in o.link_keys:
                    keyed_in[k].add(o.file_id)
        for cat in self.catalog:
            if cat.alias == cat.resource_id:
                continue
            pattern = _alias_re(cat.alias)
            for file_id in sorted(texts):
                if file_id in keyed_in.get(cat.alias.lower(), ()):
                    continue
                in_facts = cat.alias in fact_text.get(file_id, ())
                if not in_facts and not pattern.search(texts[file_id]):
                    continue
                at = captured[file_id]
                system = canonical(cat.resource_id) or ""
                self.obs_by_resource[cat.resource_id].append(
                    Observation(
                        file_id=file_id,
                        captured_at=at,
                        observed_at=at,
                        system_id=system,
                        source_system="data_catalog",
                        resource_id=cat.resource_id,
                        present=True,
                        copy="alias",
                        collection="alias",
                        attrs={"alias": cat.alias, "catalog_activity": cat.activity_id},
                        link_keys=frozenset({cat.email, cat.user_id}),
                        raw_ref=f"{file_id}#alias:{cat.alias}",
                    )
                )

    def activity(self, activity_id: str) -> Activity:
        return self.activity_by_id[activity_id]

    def resource(self, resource_id: str, via: tuple = ("oracle",)) -> LinkedResource:
        """A LinkedResource for any id (evaluation: feed gold ids instead of links)."""
        return LinkedResource(
            resource_id=resource_id,
            system_id=canonical(resource_id) or "",
            via=(via,),
            activity_ids=self._activity_ids(resource_id),
            observations=self.resource_observations(resource_id),
        )

    def _activity_ids(self, resource_id: str) -> tuple[str, ...]:
        ids = {a.activity_id for a in self.acts_by_resource.get(resource_id, ())}
        return tuple(sorted(ids, key=case_num))

    def resource_observations(self, resource_id: str) -> tuple[Observation, ...]:
        obs = self.obs_by_resource.get(resource_id, [])
        return tuple(sorted(obs, key=lambda o: (o.captured_at, o.file_id, o.raw_ref)))

    # ----- ownership -----

    def _owners(self) -> dict[str, set[str]]:
        owners: dict[str, set[str]] = defaultdict(set)
        for rid, obs in self.obs_by_resource.items():
            for o in obs:
                if o.copy == "alias":
                    continue
                owners[rid] |= {self.key_index[k] for k in o.link_keys if k in self.key_index}
        for cat in self.catalog:
            owners[cat.resource_id].add(cat.user_id)
        return owners

    # ----- channels -----

    def _key_links(self, case_id: str) -> dict[str, set[tuple]]:
        subject = self.subjects[case_id]
        links: dict[str, set[tuple]] = defaultdict(set)
        if subject is None:
            return links
        for k in subject.strong_keys:
            for o in self.obs_by_key.get(k, ()):
                links[o.resource_id].add(("key", k, o.file_id))
        return links

    def _catalog_links(self, case_id: str) -> dict[str, set[tuple]]:
        subject = self.subjects[case_id]
        links: dict[str, set[tuple]] = defaultdict(set)
        if subject is None:
            return links
        for cat in self.catalog:
            if cat.email in subject.strong_keys or cat.user_id in subject.strong_keys:
                links[cat.resource_id].add(("catalog", cat.activity_id))
        return links

    def _identity_conflict(self, resource_id: str, user_id: str) -> bool:
        for o in self.obs_by_resource.get(resource_id, ()):
            if o.copy == "alias":
                continue
            for k in o.link_keys:
                owner = self.key_index.get(k)
                if owner is not None and owner != user_id:
                    return True
                if owner is None and _is_identity_key(k):
                    return True
        return False

    # ----- worker windows -----

    def _worker_acts(self, resource_id: str) -> list[Activity]:
        return [a for a in self.acts_by_resource.get(resource_id, ()) if a.source_system == WORKER]

    def _learn_windows(self) -> dict[str, tuple[float, float, str, int]]:
        by_template: dict[str, list[float]] = defaultdict(list)
        by_family: dict[str, list[float]] = defaultdict(list)
        for c in self.case_ids:
            at = self.requests[c].reported_at
            for rid in self.key_links[c]:
                for a in self._worker_acts(rid):
                    h = _hours(a, at)
                    if abs(h) <= LEARN_RANGE_HOURS:
                        by_template[template(a)].append(h)
                        by_family[family(a)].append(h)
        windows = {}
        for a in self.activities:
            if a.source_system != WORKER:
                continue
            t = template(a)
            if t in windows:
                continue
            if samples := by_template.get(t):
                source = "template"
            elif samples := by_family.get(family(a)):
                source = "family"
            else:
                windows[t] = (*DEFAULT_WINDOW, "default", 0)
                continue
            windows[t] = (min(samples) - WINDOW_PAD_HOURS, max(samples) + WINDOW_PAD_HOURS,
                          source, len(samples))
        return windows

    def in_window(self, a: Activity, case_id: str) -> bool:
        lo, hi = self.windows.get(template(a), DEFAULT_WINDOW)[:2]
        return lo <= _hours(a, self.requests[case_id].reported_at) <= hi

    def _compatible(self, a: Activity, case_id: str) -> bool:
        if not self.in_window(a, case_id):
            return False
        if SUBMITTED in a.details:
            return STATUS_RESULT.get(self.requests[case_id].reported_status) == a.reported_result
        return True

    def _assign_workers(self):
        held: dict[str, set[str]] = {
            c: {rtype(r) for r in (*self.key_links[c], *self.catalog_links[c])}
            for c in self.case_ids
        }
        eligible = [c for c in self.case_ids if c not in self.non_deletion]
        free = sorted(
            {a.resource_id for a in self.activities if a.source_system == WORKER}
            - {rid for rid, users in self.owners.items() if users},
            key=_natural,
        )
        # resource -> {case: earliest compatible activity}
        compat: dict[str, dict[str, Activity]] = {}
        for rid in free:
            t = rtype(rid)
            options: dict[str, Activity] = {}
            for a in self._worker_acts(rid):
                for c in eligible:
                    if t not in held[c] and c not in options and self._compatible(a, c):
                        options[c] = a
            if options:
                compat[rid] = options

        assigned: dict[str, dict[str, set[tuple]]] = defaultdict(dict)  # case -> rid -> via
        ties = 0

        def candidates(rid: str) -> list[str]:
            return [c for c in compat[rid] if rtype(rid) not in held[c]]

        def give(rid: str, case_id: str, tie: bool) -> None:
            assigned[case_id][rid] = {("worker_window", compat[rid][case_id].activity_id, tie)}
            held[case_id].add(rtype(rid))
            del compat[rid]

        while compat:
            changed = True
            while changed:  # unique-candidate propagation
                changed = False
                for rid in sorted(compat, key=_natural):
                    if rid not in compat:
                        continue
                    cands = candidates(rid)
                    if not cands:
                        del compat[rid]
                    elif len(cands) == 1:
                        give(rid, cands[0], tie=False)
                        changed = True
            if not compat:
                break
            # one tie group: same type and same candidate set; earliest event first
            groups: dict[tuple, list[str]] = defaultdict(list)
            for rid in compat:
                groups[(rtype(rid), tuple(sorted(candidates(rid), key=case_num)))].append(rid)

            def first_time(rid: str) -> tuple:
                return (min(a.event_time for a in compat[rid].values()), _natural(rid))

            key = min(groups, key=lambda g: (min(first_time(r) for r in groups[g]), g))
            rids = sorted(groups[key], key=first_time)
            for case_id, rid in zip(key[1], rids, strict=False):
                give(rid, case_id, tie=True)
                ties += 1
            for rid in rids:  # leftovers of a saturated group stay free for other cases
                if rid in compat and not candidates(rid):
                    del compat[rid]
        return assigned, ties

    # ----- stale filter + output -----

    def _stale(self, rid: str, case_id: str) -> bool:
        req = self.requests[case_id]
        if any(o.captured_at >= req.requested_at for o in self.obs_by_resource.get(rid, ())):
            return False
        return not any(self.in_window(a, case_id) for a in self._worker_acts(rid))

    def link(self, case_id: str) -> list[LinkedResource]:
        if case_id in self._cache:
            return self._cache[case_id]
        vias: dict[str, set[tuple]] = defaultdict(set)
        for source in (self.key_links, self.catalog_links, self.worker_links):
            for rid, v in source.get(case_id, {}).items():
                vias[rid] |= v
        out = []
        for rid in sorted(vias):
            if self._stale(rid, case_id):
                continue
            flags = {}
            users = [c.user_id for c in self.catalog if ("catalog", c.activity_id) in vias[rid]]
            if any(self._identity_conflict(rid, u) for u in users):
                flags["identity_conflict"] = True
            out.append(
                LinkedResource(
                    resource_id=rid,
                    system_id=canonical(rid) or "",
                    via=tuple(sorted(vias[rid], key=repr)),
                    activity_ids=self._activity_ids(rid),
                    observations=self.resource_observations(rid),
                    flags=flags,
                )
            )
        self._cache[case_id] = out
        return out


@cache
def build_linker(non_deletion: frozenset[str] = frozenset()) -> Linker:
    return Linker.from_dataset(non_deletion)


def link_case(
    request: DeletionRequest, non_deletion: frozenset[str] = frozenset()
) -> list[LinkedResource]:
    return build_linker(non_deletion).link(request.case_id)
