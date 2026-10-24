"""Test-suite guards: tests must never grow the committed usage ledger."""

import pytest

from ghost import llm


@pytest.fixture(autouse=True, scope="session")
def _isolated_usage_ledger(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(llm, "LEDGER_PATH", tmp_path_factory.mktemp("ledger") / "usage_ledger.jsonl")
    yield
    mp.undo()
