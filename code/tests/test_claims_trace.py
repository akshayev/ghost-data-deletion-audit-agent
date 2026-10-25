"""Every code reference in code/docs/CLAIMS_TRACE.md must exist (file, and symbol if named)."""

import re

from ghost import paths

TRACE = paths.CODE / "docs" / "CLAIMS_TRACE.md"
REF = re.compile(r"`(code/[^`:]+?)(?:(::|:)([A-Za-z_][A-Za-z0-9_.]*))?`")


def references():
    return REF.findall(TRACE.read_text(encoding="utf-8"))


def defined(text: str, symbol: str) -> bool:
    name = symbol.split(".")[-1]
    return bool(re.search(rf"^\s*(?:def|class)\s+{re.escape(name)}\b|^{re.escape(name)}\s*[:=]",
                          text, re.M))


def test_trace_cites_many_claims():
    assert len(references()) > 80


def test_every_cited_file_and_symbol_exists():
    missing = []
    for path, sep, symbol in references():
        file = paths.REPO_ROOT / path
        if not file.exists():
            missing.append(path)
            continue
        if symbol and file.is_file() and not defined(file.read_text(encoding="utf-8"), symbol):
            missing.append(f"{path}{sep}{symbol}")
    assert not missing, missing
