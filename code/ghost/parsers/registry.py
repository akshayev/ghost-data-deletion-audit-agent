"""Pick a parser for every evidence file listed in evidence_files.csv.

Dispatch is by extension, then by content (each parser sniffs structure, never
file names). PNG screenshots go through the cached vision extraction (images.py).
"""

from functools import cache

from ghost import loaders, paths
from ghost.observation import Coverage, FileFacts, Observation
from ghost.parsers.common import FileCtx, FileParse, reference_observations
from ghost.parsers.images import parse_image
from ghost.parsers.json_records import parse_json
from ghost.parsers.logs import parse_log
from ghost.parsers.pdf import parse_pdf
from ghost.parsers.sql import parse_sql
from ghost.parsers.text import parse_text

PARSERS = {
    ".sql": parse_sql,
    ".json": parse_json,
    ".log": parse_log,
    ".txt": parse_text,
    ".pdf": parse_pdf,
    ".png": parse_image,
}


def parse_file(ctx: FileCtx) -> FileParse:
    path = paths.DATASET / ctx.path
    ext = path.suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        return FileParse(ctx.file_id, ctx.path, f"unknown:{ext}")
    result = parser(ctx, path)
    result.observations += reference_observations(result.observations)
    return result


@cache
def parse_results() -> dict[str, FileParse]:
    """file_id -> FileParse for every file in the manifest (manifest order)."""
    out = {}
    for ef in loaders.load_evidence_files():
        ctx = FileCtx(ef.file_id, ef.file_path, ef.captured_at)
        out[ef.file_id] = parse_file(ctx)
    return out


@cache
def parse_all() -> tuple[tuple[Observation, ...], tuple[Coverage, ...], tuple[FileFacts, ...]]:
    results = parse_results().values()
    return (
        tuple(o for r in results for o in r.observations),
        tuple(c for r in results for c in r.coverages),
        tuple(f for r in results for f in r.facts),
    )
