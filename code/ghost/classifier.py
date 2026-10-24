"""Request intent: does request_text authorize deletion (or ask to verify one)?

Rules always run (fallback + cross-check). The LLM labels every unique text in batches and
is cached by ghost.llm, so reruns are free. final = LLM label when available, else the rule
label (UNSURE -> DELETION: the dataset frames these as deletion requests).
"""

import json
import re
from dataclasses import dataclass

from ghost import llm, loaders, paths

PROMPT_PATH = paths.CODE / "prompts" / "request_classify.md"
PURPOSE = "classify_request"
BATCH_SIZE = 40
LABELS = ("DELETION", "NOT_DELETION")
TEXT_MODEL_ENV = "GEMINI_TEXT_MODEL"  # falls back to GEMINI_MODEL when unset

_FLAGS = re.IGNORECASE


def _compile(patterns):
    return [re.compile(p, _FLAGS) for p in patterns]


# Explicit "keep the account / do not delete" phrasing (general patterns, EN/ES/PT/FR/JA/KO).
KEEP = _compile(
    [
        r"\b(?:keep|leave)\b[^.;]{0,30}\b(?:account|profile|records?|history)\b",
        r"\b(?:leave|keep|remain)\b[^.;]{0,25}\b(?:active|in place|as it is|intact)\b",
        r"\b(?:do not|don'?t|dont|never)\s+(?:close|delete|remove|erase)\b",
        r"\bnot (?:asking|requesting|a request)\b[^.;]{0,30}\b(?:delet|erasure|erase|removal|clos)",
        r"\bwithout\s+(?:removing|deleting|closing|erasing)\b",
        r"\bmarketing (?:lists?|emails?|messages?)?\s*only\b|\bonly (?:the )?marketing\b",
        r"\bcontinue (?:to )?us(?:e|ing)\b",
        r"\bstill (?:use|need) (?:the|this|my) (?:account|service)\b",
        r"\bno (?:quiero |solicito |deseo )?(?:eliminar|borrar|cerrar)\b",
        r"\b(?:mantener|conservar|dejar)\b[^.;]{0,15}\bcuenta\b",
        r"\bn[ãa]o (?:quero |pe[çc]o |desejo )?(?:excluir|apagar|encerrar|eliminar)\b",
        r"\b(?:manter|deixar)\b[^.;]{0,15}\bconta\b",
        r"\bne (?:pas|veux pas|souhaite pas)\s+(?:supprimer|effacer|fermer)\b",
        r"\b(?:garder|conserver|laisser)\b[^.;]{0,15}\bcompte\b",
        r"削除しないで|削除は不要|삭제하지\s*(?:마|말)",
    ]
)

DELETION = _compile(
    [
        r"\b(?:delet\w*|eras\w+|remov\w+|purg\w+|wip\w+|clos(?:e|ing|ure)\b[^.;]{0,15}account)",
        r"\b(?:eliminar|eliminaci\w+|elimine\w*|borrar|borre\w*|cancelar\b[^.;]{0,10}cuenta)",
        r"\b(?:exclus[ãa]o|excluir|exclua\w*|apagar|elimina[çc][ãa]o)\b",
        r"\b(?:suppression|supprim\w+|effac\w+|clôtur\w+)",
        r"削除|消去|삭제|탈퇴",
        r"\b(?:verify|audit|confirm|re-?check|finish)\b[^.;]{0,60}\b(?:gone|deleted|removed|erased)\b",
        r"\b(?:request failed|still existed|still exists?)\b",
    ]
)


def rule_label(text: str) -> str:
    if any(p.search(text) for p in KEEP):
        return "NOT_DELETION"
    if any(p.search(text) for p in DELETION):
        return "DELETION"
    return "UNSURE"


@dataclass(frozen=True)
class Decision:
    rule_label: str
    llm_label: str | None
    final: str
    reason: str
    source: str  # "llm" | "rules"


def decide(text: str, llm_result: tuple[str, str] | None) -> Decision:
    rule = rule_label(text)
    if llm_result:
        label, reason = llm_result
        return Decision(rule, label, label, reason, "llm")
    return Decision(rule, None, "NOT_DELETION" if rule == "NOT_DELETION" else "DELETION",
                    "rule fallback", "rules")


# ---------- LLM ----------


def _valid(data, n: int) -> dict[int, tuple[str, str]]:
    """Entries with a unique in-range id and a valid label; duplicated ids are dropped."""
    out: dict[int, tuple[str, str]] = {}
    seen: set = set()
    items = data.get("results") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        i = item.get("id") if isinstance(item, dict) else None
        if not isinstance(i, int) or isinstance(i, bool):
            continue
        if i in seen:
            out.pop(i, None)
            continue
        seen.add(i)
        if 0 <= i < n and item.get("label") in LABELS:
            out[i] = (item["label"], str(item.get("reason", ""))[:120])
    return out


def _ask(batch: list[str], use_llm: bool):
    messages = [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {
            "role": "user",
            "content": json.dumps(
                [{"id": i, "text": t} for i, t in enumerate(batch)], ensure_ascii=False
            ),
        },
    ]
    try:
        result = llm.chat(
            messages,
            purpose=PURPOSE,
            providers=("gemini",),
            offline=not use_llm,
            model_env=TEXT_MODEL_ENV,
        )
    except llm.LLMError:
        return None
    return result.data


def llm_labels(texts: list[str], use_llm: bool = True) -> dict[str, tuple[str, str]]:
    """text -> (label, reason) for every text the model labelled validly. A batch with a
    missing/duplicate/invalid id is retried once; leftovers are left to the rules."""
    unique = list(dict.fromkeys(texts))
    out: dict[str, tuple[str, str]] = {}
    for start in range(0, len(unique), BATCH_SIZE):
        batch = unique[start : start + BATCH_SIZE]
        got: dict[int, tuple[str, str]] = {}
        for _attempt in range(2):
            got = _valid(_ask(batch, use_llm), len(batch))
            if len(got) == len(batch):
                break
        out.update({batch[i]: v for i, v in got.items()})
    return out


def classify_texts(texts: list[str], use_llm: bool = True) -> dict[str, Decision]:
    labels = llm_labels(texts, use_llm)
    return {t: decide(t, labels.get(t)) for t in dict.fromkeys(texts)}


def classify_requests(use_llm: bool = True) -> dict[str, Decision]:
    """Decision for every distinct request text over both splits. use_llm=False replays the
    cache (no network) and falls back to rules for anything not cached."""
    texts = [r.request_text for s in ("sample", "full") for r in loaders.load_requests(s)]
    return classify_texts(texts, use_llm)


def non_deletion_cases(decisions: dict[str, Decision]) -> frozenset[str]:
    return frozenset(
        r.case_id
        for s in ("sample", "full")
        for r in loaders.load_requests(s)
        if decisions[r.request_text].final == "NOT_DELETION"
    )


def render_report(decisions: dict[str, Decision]) -> str:
    cases: dict[str, list[str]] = {}
    for s in ("sample", "full"):
        for r in loaders.load_requests(s):
            cases.setdefault(r.request_text, []).append(r.case_id)
    llm_n = sum(d.source == "llm" for d in decisions.values())
    agree = [d for d in decisions.values() if d.llm_label and d.rule_label == d.llm_label]
    lines = [
        "# Classifier report",
        "",
        f"- unique texts: {len(decisions)}  (LLM-labelled: {llm_n}, rule fallback: "
        f"{len(decisions) - llm_n})",
        f"- final DELETION: {sum(d.final == 'DELETION' for d in decisions.values())}, "
        f"NOT_DELETION: {sum(d.final == 'NOT_DELETION' for d in decisions.values())}",
        f"- rules vs LLM agreement: {len(agree)}/{llm_n}",
        "",
        "## NOT_DELETION texts",
        "",
    ]
    for t, d in decisions.items():
        if d.final == "NOT_DELETION":
            lines.append(f"- {cases[t]} rule={d.rule_label} llm={d.llm_label} ({d.reason}): {t}")
    lines += ["", "## Rule / LLM disagreements", ""]
    dis = [(t, d) for t, d in decisions.items() if d.llm_label and d.rule_label != d.llm_label]
    lines += [
        f"- {cases[t][:3]} rule={d.rule_label} llm={d.llm_label} ({d.reason}): {t}"
        for t, d in dis
    ] or ["none"]
    return "\n".join(lines) + "\n"


REPORT = paths.EVAL_RUNS / "classifier_report.md"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Classify every request text (cache-backed)")
    parser.add_argument("--no-llm", action="store_true", help="replay the cache / rules only")
    args = parser.parse_args(argv)
    decisions = classify_requests(use_llm=not args.no_llm)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render_report(decisions), encoding="utf-8")
    print(render_report(decisions))
    print(f"written: {REPORT}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
