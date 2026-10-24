"""JSON evidence: snapshot bundles, service exports, ledgers and flat collections."""

import json
from collections.abc import Mapping
from pathlib import Path

from ghost.parsers.common import (
    FileCtx,
    FileParse,
    coverage,
    is_ledger_record,
    ledger_coverages,
    ledger_observation,
    make_observation,
    safe_ts,
    split_record,
)


def _add(result: FileParse, obs) -> None:
    if obs is None:
        result.unparsed += 1
    else:
        result.observations.append(obs)


def _ledger_rows(ctx: FileCtx, result: FileParse, records, collection: str, ref: str) -> None:
    new = []
    for i, r in enumerate(records):
        obs = ledger_observation(ctx, r, collection, f"{ref}[{i}]")
        _add(result, obs)
        if obs:
            new.append(obs)
    result.coverages += ledger_coverages(ctx, new, collection)


def _bundle(ctx: FileCtx, d: Mapping, result: FileParse) -> None:
    for si, snap in enumerate(d["snapshots"]):
        store, coll, records = snap["data_store"], snap["collection"], snap.get("records", [])
        ref = f"{ctx.path}#snapshots[{si}]"
        if records and all(is_ledger_record(r) for r in records):
            _ledger_rows(ctx, result, records, coll, ref)
            continue
        when = safe_ts(snap.get("captured_at"), ctx.captured_at)
        result.coverages.append(coverage(ctx, store, coll, complete=True, observed_at=when))
        for i, r in enumerate(records):
            fields, attrs = split_record(r)
            _add(
                result,
                make_observation(
                    ctx,
                    resource_id=r.get("resource_id"),
                    present=True,
                    copy="bundle",
                    collection=coll,
                    source_system=store,
                    fields=fields,
                    attrs=attrs,
                    raw_ref=f"{ref}.records[{i}]",
                    observed_at=when,
                ),
            )


def _service_export(ctx: FileCtx, d: Mapping, result: FileParse) -> None:
    for system, block in d["service_exports"].items():
        for coll, rows in block.items():
            result.coverages.append(coverage(ctx, system, coll, complete=True))
            for i, r in enumerate(rows):
                rid = r.get("resource_id")
                body = {k: v for k, v in r.items() if k not in ("profile", "hits")}
                fields, attrs = split_record(body)
                present: bool | None = True
                if "status_code" in r:
                    code = r["status_code"]
                    present = True if code == 200 else False if code == 404 else None
                    if isinstance(r.get("profile"), Mapping):
                        pf, pa = split_record(r["profile"])
                        fields.update(pf)
                        attrs.update({f"profile.{k}": v for k, v in pa.items()})
                if "hits" in r:
                    hits = r["hits"] or {}
                    total = hits.get("total") or 0
                    present = total > 0
                    attrs["hits_total"] = total
                    for doc in hits.get("documents", []):
                        if doc.get("resource_id") in (None, rid):
                            df, _ = split_record(doc)
                            fields.update({k: v for k, v in df.items() if v is not None})
                _add(
                    result,
                    make_observation(
                        ctx,
                        resource_id=rid,
                        present=present,
                        copy="export",
                        collection=coll,
                        source_system=system,
                        fields=fields,
                        attrs=attrs,
                        raw_ref=f"{ctx.path}#{system}.{coll}[{i}]",
                        observed_at=safe_ts(r.get("observed_at"), ctx.captured_at),
                    ),
                )


def _flat_collection(ctx: FileCtx, d: Mapping, result: FileParse) -> None:
    coll = d["collection"]
    when = safe_ts(d.get("captured_at"), ctx.captured_at)
    systems = set()
    for i, r in enumerate(d["records"]):
        fields, attrs = split_record(r)
        obs = make_observation(
            ctx,
            resource_id=r.get("resource_id"),
            present=True,
            copy="collection",
            collection=coll,
            source_system=d.get("source", ""),
            fields=fields,
            attrs=attrs,
            raw_ref=f"{ctx.path}#records[{i}]",
            observed_at=when,
        )
        _add(result, obs)
        if obs:
            systems.add(obs.system_id)
    for s in sorted(systems):
        result.coverages.append(coverage(ctx, s, coll, complete=True, observed_at=when))


def parse_json(ctx: FileCtx, path: Path) -> FileParse:
    d = json.loads(path.read_text(encoding="utf-8"))
    result = FileParse(ctx.file_id, ctx.path, "json:unknown")
    if not isinstance(d, Mapping):
        return result
    if isinstance(d.get("snapshots"), list):
        result.family = "json:bundle"
        _bundle(ctx, d, result)
    elif isinstance(d.get("service_exports"), Mapping):
        result.family = "json:service_export"
        _service_export(ctx, d, result)
    elif isinstance(d.get("records"), list) and all(is_ledger_record(r) for r in d["records"]):
        result.family = "json:ledger"
        _ledger_rows(ctx, result, d["records"], d.get("origin", "records"), f"{ctx.path}#records")
    elif isinstance(d.get("records"), list) and "collection" in d:
        result.family = "json:collection"
        _flat_collection(ctx, d, result)
    return result

