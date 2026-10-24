"""SQLite-compatible SQL snapshot exports -> Observations + Coverage.

Tables are recognised by their columns, never by file name:
  * snapshot_scope(origin_system, captured_at)       -> Coverage per row
  * (resource_id, origin_system, collection_name, record_json, ...) -> JSON rows
  * any other table with a resource_id column         -> typed rows
"""

import json
import re
import sqlite3
from pathlib import Path

from ghost.parsers.common import (
    FileCtx,
    FileParse,
    coverage,
    make_observation,
    safe_ts,
    split_record,
)

RECORD_JSON_COLS = {"resource_id", "origin_system", "collection_name", "record_json"}
ROW_TIME_COLS = ("updated_at", "indexed_at", "observed_at", "recorded_at")
_STEM_RE = re.compile(r"_(primary|[a-z_]*replica[a-z_]*)$")


def copy_for_table(table: str) -> str:
    if "replica" in table:
        return f"replica:{table}"
    return "primary"


def table_stem(table: str) -> str:
    return _STEM_RE.sub("", table)


def _tables(db: sqlite3.Connection) -> dict[str, list[str]]:
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {t: [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')] for t in names}


def parse_sql(ctx: FileCtx, path: Path) -> FileParse:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(path.read_text(encoding="utf-8"))
    tables = _tables(db)
    result = FileParse(ctx.file_id, ctx.path, "sql")
    families: set[str] = set()

    # snapshot_scope -> one complete Coverage per covered system
    if "snapshot_scope" in tables:
        families.add("inventory")
        for row in db.execute("SELECT origin_system FROM snapshot_scope"):
            result.coverages.append(coverage(ctx, row[0], "snapshot_scope", complete=True))

    json_tables = [t for t, cols in tables.items() if RECORD_JSON_COLS <= set(cols)]
    typed_tables = [
        t
        for t, cols in tables.items()
        if t != "snapshot_scope" and t not in json_tables and "resource_id" in cols
    ]
    other = [t for t in tables if t != "snapshot_scope" and t not in json_tables + typed_tables]
    for t in other:  # unknown table shape: count its rows as unparsed
        result.unparsed += db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]

    # record_json tables (primary + replicas of a mixed store)
    if json_tables:
        families.add("record_json")
        systems: set[str] = set()
        for t in json_tables:
            for row in db.execute(f'SELECT rowid, * FROM "{t}" ORDER BY rowid'):
                systems.add(row["origin_system"])
                record = json.loads(row["record_json"])
                fields, attrs = split_record(record)
                obs = make_observation(
                    ctx,
                    resource_id=row["resource_id"],
                    present=True,
                    copy=copy_for_table(t),
                    collection=row["collection_name"],
                    source_system=row["origin_system"],
                    fields=fields,
                    attrs=attrs,
                    raw_ref=f"{ctx.path}#{t}:{row['rowid']}",
                    observed_at=safe_ts(row["updated_at"], ctx.captured_at),
                )
                if obs:
                    result.observations.append(obs)
                else:
                    result.unparsed += 1
        # each table is a full dump: it covers every system the store holds
        for t in json_tables:
            for s in sorted(systems):
                result.coverages.append(coverage(ctx, s, t, complete=True))

    # typed tables (inventory tables, *_primary / *_replica tables)
    stem_systems: dict[str, set[str]] = {}
    for t in typed_tables:
        if "snapshot_scope" not in tables:
            families.add("typed")
        for row in db.execute(f'SELECT rowid, * FROM "{t}" ORDER BY rowid'):
            record = {k: row[k] for k in row.keys() if k != "rowid"}
            fields, attrs = split_record(record)
            when = next((record[c] for c in ROW_TIME_COLS if record.get(c)), None)
            obs = make_observation(
                ctx,
                resource_id=record["resource_id"],
                present=True,
                copy=copy_for_table(t),
                collection=t,
                source_system="",
                fields=fields,
                attrs=attrs,
                raw_ref=f"{ctx.path}#{t}:{row['rowid']}",
                observed_at=safe_ts(when, ctx.captured_at),
            )
            if obs:
                result.observations.append(obs)
                stem_systems.setdefault(table_stem(t), set()).add(obs.system_id)
            else:
                result.unparsed += 1
    if "snapshot_scope" not in tables:
        for t in typed_tables:
            for s in sorted(stem_systems.get(table_stem(t), set())):
                result.coverages.append(coverage(ctx, s, t, complete=True))

    db.close()
    result.family = "sql:" + "+".join(sorted(families)) if families else "sql:empty"
    return result

