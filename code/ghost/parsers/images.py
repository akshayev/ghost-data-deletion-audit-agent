"""Screenshot (PNG) evidence -> Observations.

    python code/ghost/parsers/images.py --extract

A vision model only transcribes each screenshot into JSON (prompts/screenshot_extract.md);
turning that into Observations is plain code with hallucination guards. Extractions are
cached on disk (code/cache/llm), so parsing is reproducible without a key. Without a cached
extraction the parser falls back to OCR (if tesseract is installed) or skips the image.
"""

import argparse
import importlib
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

if __name__ == "__main__":  # running as a script: import from code/, not this folder
    sys.path[0] = str(Path(__file__).resolve().parents[2])

from ghost import llm, loaders, paths  # noqa: E402
from ghost.extract import (  # noqa: E402
    EMAIL_RE,
    USER_ID_RE,
    find_resource_ids,
    keys_in_text,
    link_keys_from,
)
from ghost.identity import IdentityResolver  # noqa: E402
from ghost.normalize import norm_email, norm_hash, norm_phone  # noqa: E402
from ghost.observation import FileFacts, Observation  # noqa: E402
from ghost.parsers.common import FileCtx, FileParse, make_observation  # noqa: E402
from ghost.systems import RESOURCE_ID_RE  # noqa: E402

PROMPT_PATH = paths.CODE / "prompts" / "screenshot_extract.md"
PURPOSE = "screenshot_extract"
USER_MESSAGE = "Transcribe this screenshot. Return only the JSON object."
PANEL_SOURCES = ("detail_panel", "page_header")
MAX_EMAIL_EDITS = 2

# Use the vision model on a cache miss? Off by default so parsing never touches the network;
# `--extract` (and later the full pipeline) switches it on.
USE_LLM = False

_DOT_ID_RE = re.compile(r"^([a-z_]+)[.\-\s](\d+)$")


@dataclass
class ImageStats:
    records: int = 0
    mentions: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    repaired_ids: list[str] = field(default_factory=list)
    suspect_emails: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "records": self.records,
            "mentions": self.mentions,
            "dropped_ids": list(self.dropped_ids),
            "repaired_ids": list(self.repaired_ids),
            "suspect_emails": list(self.suspect_emails),
        }


# ---------- guards ----------


def _edit_distance_within(a: str, b: str, limit: int) -> bool:
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def suspect_match(email: str, known: frozenset[str]) -> str | None:
    """The identity email this one is probably a misread of, or None. An email that is
    itself known, or whose local part is more than 2 edits from every known one, is fine."""
    if email in known:
        return None
    local = email.partition("@")[0]
    for candidate in sorted(known):
        if _edit_distance_within(local, candidate.partition("@")[0], MAX_EMAIL_EDITS):
            return candidate
    return None


def _valid_id(raw, stats: ImageStats) -> str | None:
    """Resource id as a valid id, repairing a dot/hyphen where an underscore belongs.
    Anything else is dropped and counted."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    rid = raw.strip()
    if RESOURCE_ID_RE.fullmatch(rid):
        return rid
    m = _DOT_ID_RE.match(rid)
    if m and RESOURCE_ID_RE.fullmatch(f"{m.group(1)}_{m.group(2)}"):
        fixed = f"{m.group(1)}_{m.group(2)}"
        stats.repaired_ids.append(rid)
        return fixed
    stats.dropped_ids.append(rid)
    return None


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_fields(raw: dict) -> dict:
    out: dict = {}
    if name := _text(raw.get("name")):
        out["name"] = name
    if (email := norm_email(_text(raw.get("email")))) and EMAIL_RE.fullmatch(email):
        out["email"] = email
    if phone := norm_phone(_text(raw.get("phone"))):
        out["phone"] = phone
    if h := norm_hash(_text(raw.get("email_hash"))):
        out["email_hash"] = h
    if (ref := _text(raw.get("user_ref"))) and USER_ID_RE.fullmatch(ref):
        out["user_ref"] = ref
    return out


# ---------- conversion ----------


def convert(
    ctx: FileCtx, data: dict, known_emails: frozenset[str]
) -> tuple[list[Observation], list[FileFacts], ImageStats]:
    screen = data.get("screen") or {}
    product = _text(screen.get("product")) or ""
    title = _text(screen.get("title")) or ""
    stats = ImageStats()

    parsed = []  # (index, raw id, valid id|None, row_source, fields, record)
    for i, r in enumerate(data.get("records") or []):
        raw_id = _text(r.get("resource_id"))
        rid = _valid_id(raw_id, stats)
        source = r.get("row_source") or "table_row"
        parsed.append((i, raw_id, rid, source, _clean_fields(r.get("fields") or {}), r))

    panels: dict[str, dict] = defaultdict(dict)  # resource_id -> fields from its panels/headers
    for _, _, rid, source, fields, _ in parsed:
        if rid and source in PANEL_SOURCES:
            for k, v in fields.items():
                panels[rid].setdefault(k, v)

    observations: list[Observation] = []
    record_ids = set()
    null_id_fields: list[dict] = []
    for i, raw_id, rid, source, fields, r in parsed:
        if rid is None:
            if raw_id is None:  # a record with no id (not a dropped one)
                null_id_fields.append(fields)
            continue
        record_ids.add(rid)
        merged = {**fields, **{k: v for k, v in panels.get(rid, {}).items() if k not in fields}}
        link_keys = link_keys_from(fields, panels.get(rid, {}))
        attrs: dict = {}
        if (status := _text(r.get("status"))) is not None:
            attrs["status"] = status
        for k, v in (r.get("attributes") or {}).items():
            attrs[str(k)] = "" if v is None else str(v)
        attrs["row_source"] = source
        if (email := merged.get("email")) and (near := suspect_match(email, known_emails)):
            attrs["suspect_email"] = near
            stats.suspect_emails.append(email)
        o = make_observation(
            ctx,
            resource_id=rid,
            present=True,
            copy="image",
            collection=title,
            source_system=product,
            fields=merged,
            attrs=attrs,
            raw_ref=f"{ctx.path}#record:{i}",
        )
        assert o is not None  # id was validated above
        observations.append(replace(o, link_keys=link_keys))
        stats.records += 1

    seen_mentions: set[str] = set()
    for raw in data.get("mentions") or []:
        rid = _valid_id(raw, stats)
        if rid is None or rid in record_ids or rid in seen_mentions:
            continue
        seen_mentions.add(rid)
        o = make_observation(
            ctx,
            resource_id=rid,
            present=None,
            copy="image",
            collection=title,
            source_system=product,
            fields={},
            attrs={"row_source": "activity_feed"},
            raw_ref=f"{ctx.path}#mention:{rid}",
        )
        assert o is not None
        observations.append(o)
        stats.mentions += 1

    facts = _facts(ctx, data.get("references") or {}, null_id_fields)
    return observations, facts, stats


def _strings(values) -> frozenset[str]:
    return frozenset(s for v in (values or []) if (s := _text(v)))


def _facts(ctx: FileCtx, refs: dict, null_id_fields: list[dict]) -> list[FileFacts]:
    user_ids = {u for u in _strings(refs.get("user_refs")) if USER_ID_RE.fullmatch(u)}
    emails = {f["email"] for f in null_id_fields if "email" in f}
    user_ids |= {f["user_ref"] for f in null_id_fields if "user_ref" in f}
    for other in _strings(refs.get("other")):
        emails |= {norm_email(m) for m in EMAIL_RE.findall(other)}
    facts = FileFacts(
        file_id=ctx.file_id,
        captured_at=ctx.captured_at,
        user_ids=frozenset(user_ids),
        order_refs=_strings(refs.get("order_refs")),
        retain_until=(),
        emails=frozenset(emails),
        ticket_refs=_strings(refs.get("ticket_refs")),
        file_names=_strings(refs.get("file_names")),
    )
    empty = not (
        facts.user_ids or facts.order_refs or facts.emails or facts.ticket_refs or facts.file_names
    )
    return [] if empty else [facts]


# ---------- extraction (vision model, cache-backed) ----------


@cache
def known_emails() -> frozenset[str]:
    resolver = IdentityResolver(loaders.load_identities(), loaders.load_audit_changes())
    return frozenset(k for k in resolver.all_keys_index() if "@" in k)


def load_extraction(path: Path, use_llm: bool) -> llm.LLMResult | None:
    """The vision model's JSON for this image: from the cache, or (use_llm) a fresh call."""
    messages = [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": USER_MESSAGE},
    ]
    try:
        result = llm.chat(
            messages,
            purpose=PURPOSE,
            image_bytes=path.read_bytes(),
            providers=("gemini",),
            offline=not use_llm,
        )
    except llm.LLMError:
        return None
    return result if isinstance(result.data, dict) else None


# ---------- offline fallback ----------


def ocr_lines(path: Path) -> list[str] | None:
    """Text lines via tesseract, or None when OCR is not installed."""
    if not shutil.which("tesseract"):
        return None
    try:
        pytesseract = importlib.import_module("pytesseract")
        pil_image = importlib.import_module("PIL.Image")
    except ImportError:
        return None
    text = pytesseract.image_to_string(pil_image.open(path))
    return [line for line in text.splitlines() if line.strip()]


def _ocr_observations(ctx: FileCtx, lines: list[str]) -> list[Observation]:
    out = []
    for n, line in enumerate(lines):
        keys = frozenset(keys_in_text(line))
        for rid in dict.fromkeys(find_resource_ids(line)):
            o = make_observation(
                ctx,
                resource_id=rid,
                present=None,
                copy="image",
                collection="ocr",
                source_system="",
                fields={},
                attrs={"row_source": "ocr_line"},
                raw_ref=f"{ctx.path}#ocr:{n}",
            )
            if o is not None:
                out.append(replace(o, link_keys=keys))
    return out


# ---------- parser entry point ----------


def parse_image(ctx: FileCtx, path: Path, use_llm: bool | None = None) -> FileParse:
    use_llm = USE_LLM if use_llm is None else use_llm
    result = load_extraction(path, use_llm)
    if result is not None:
        observations, facts, stats = convert(ctx, result.data, known_emails())
        parse = FileParse(ctx.file_id, ctx.path, "image:vision", observations, facts=facts)
        parse.stats = {
            **stats.as_dict(),
            "title": (result.data.get("screen") or {}).get("title"),
            "cache_hit": result.cache_hit,
            "model": result.model,
        }
        return parse
    lines = ocr_lines(path)
    if lines is not None:
        return FileParse(ctx.file_id, ctx.path, "image:ocr", _ocr_observations(ctx, lines))
    return FileParse(ctx.file_id, ctx.path, "image:skipped")


# ---------- CLI ----------


def extract_all() -> int:
    rows = []
    for ef in loaders.load_evidence_files():
        if not ef.file_path.lower().endswith(".png"):
            continue
        ctx = FileCtx(ef.file_id, ef.file_path, ef.captured_at)
        rows.append(parse_image(ctx, paths.DATASET / ef.file_path, use_llm=True))
    print(f"{'file_id':<12} {'family':<14} {'rec':>4} {'men':>4}  {'src':<6} screen / flags")
    for r in rows:
        s = r.stats
        src = "cache" if s.get("cache_hit") else "vision"
        detail = f"{s.get('title')!r}"
        if s.get("dropped_ids"):
            detail += f" dropped={s['dropped_ids']}"
        if s.get("repaired_ids"):
            detail += f" repaired={s['repaired_ids']}"
        if s.get("suspect_emails"):
            detail += f" suspect={s['suspect_emails']}"
        print(
            f"{r.file_id:<12} {r.family:<14} {s.get('records', 0):>4} {s.get('mentions', 0):>4}  "
            f"{src if s else '-':<6} {detail}"
        )
    totals = defaultdict(int)
    for r in rows:
        totals["records"] += r.stats.get("records", 0)
        totals["mentions"] += r.stats.get("mentions", 0)
        totals["dropped"] += len(r.stats.get("dropped_ids", []))
        totals["suspect"] += len(r.stats.get("suspect_emails", []))
    skipped = [r.file_id for r in rows if r.family == "image:skipped"]
    print(f"totals: {dict(totals)}  skipped: {skipped}")
    return 1 if skipped else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screenshot extraction")
    parser.add_argument("--extract", action="store_true", help="extract all PNGs (cache-backed)")
    args = parser.parse_args(argv)
    if args.extract:
        return extract_all()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
