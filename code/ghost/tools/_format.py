"""Formatting and schema helpers shared by the tool modules."""

import json

_STR = {"type": "string"}
_TIME = {"type": ["string", "null"], "description": "ISO-8601 cutoff; null = no cutoff"}

MAX_RESULT_CHARS = 3600  # ~900 tokens at 4 chars/token


def _obj(required: dict, optional: dict | None = None) -> dict:
    return {
        "type": "object",
        "properties": {**required, **(optional or {})},
        "required": list(required),
        "additionalProperties": False,
    }


def _out(type_: str, description: str) -> dict:
    return {"type": type_, "description": description}


def _fit(result: dict, limit: int = MAX_RESULT_CHARS) -> dict:
    """Shrink the longest list in the result until its JSON fits; say so in 'truncated'."""
    out, cut = dict(result), set()
    while len(json.dumps(out)) > limit:
        lists = [(len(json.dumps(v)), k) for k, v in out.items()
                 if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        _, key = max(lists)
        out[key] = out[key][: max(1, len(out[key]) * 2 // 3)]
        cut.add(key)
        out["truncated"] = sorted(cut)
    return out


def _state_text(s) -> str:
    if s.unknown:
        return "no capture <= audit_at (assumed compliant)"
    parts = ["present" if s.present else "absent"]
    if s.present and s.exposed:
        parts.append("identifiers exposed")
    if s.active:
        parts.append("flow active")
    if s.risk:
        parts.append("restore allowed")
    return ", ".join(parts)


def _step_text(s: dict) -> str:
    ev = f" @{s['evidence_id']}" if s["evidence_id"] else ""
    return f"{s['step_no']}. {s['phase'][0]} {s['operation']} {s['resource_id']}{ev}"
