"""PDF documents (text is extractable; no OCR needed).

Every resource id in the document becomes an Observation(present=True) whose
link keys are all identifiers in the whole document. A document with user ids
but no resource ids (e.g. a retention register) becomes a FileFacts entry.
"""

import re
from dataclasses import replace
from pathlib import Path

from pypdf import PdfReader

from ghost.extract import USER_ID_RE, find_resource_ids, keys_in_text, phones_in_text
from ghost.observation import FileFacts
from ghost.parsers.common import FileCtx, FileParse, coverage, make_observation

ORDER_RE = re.compile(r"\bORD-\d+\b")
RETAIN_RE = re.compile(r"retain until (\d{1,2} [A-Za-z]{3} \d{4})", re.IGNORECASE)


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def parse_pdf(ctx: FileCtx, path: Path) -> FileParse:
    text = pdf_text(path)
    result = FileParse(ctx.file_id, ctx.path, "pdf")
    doc_keys = frozenset(keys_in_text(text) | phones_in_text(text))
    retain = tuple(RETAIN_RE.findall(text))
    resource_ids = list(dict.fromkeys(find_resource_ids(text)))
    for rid in resource_ids:
        attrs = {"doc_text_excerpt": text[:300]}
        if retain:
            attrs["retain_until"] = list(retain)
        obs = make_observation(
            ctx,
            resource_id=rid,
            present=True,
            copy="pdf",
            collection="document",
            source_system="",
            fields={},
            attrs=attrs,
            raw_ref=f"{ctx.path}#document",
        )
        if obs:
            # every identifier in the document links every resource it mentions
            result.observations.append(replace(obs, link_keys=obs.link_keys | doc_keys))
    for s in sorted({o.system_id for o in result.observations}):
        result.coverages.append(coverage(ctx, s, "document", complete=False))
    user_ids = frozenset(USER_ID_RE.findall(text))
    if not resource_ids and user_ids:
        result.family = "pdf:facts"
        result.facts.append(
            FileFacts(
                file_id=ctx.file_id,
                captured_at=ctx.captured_at,
                user_ids=user_ids,
                order_refs=frozenset(ORDER_RE.findall(text)),
                retain_until=retain,
                emails=frozenset(k for k in doc_keys if "@" in k),
            )
        )
    return result
