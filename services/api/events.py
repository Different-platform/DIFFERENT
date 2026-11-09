"""SSE event payloads.

These mirror `StreamEvent` in `ui-template/src/lib/differ/mock-stream.ts`
exactly, so the real SSE reader can replace `mockAsk` without touching a UI
component. The frontend's shape is authoritative here rather than the
orchestrator sketch in design.md, which emits a whole-string `answer` event
and therefore cannot produce the token-by-token streaming the UI requires.

One addition beyond the frontend's union: `error`. The pipeline can fail
(provider timeout, database loss) and the UI needs to say so rather than
hanging on a stream that stopped arriving.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from services.api.domain import Citation, RouteIntent, Verdict


def _json_safe(value: Any) -> Any:
    """Make Decimals JSON-serialisable without losing fractional codes.

    Value-label codes are Decimal because sentinels like BMI 99.9 are
    fractional; an int cast here would reintroduce the truncation the schema
    was fixed to avoid.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def route(intent: RouteIntent) -> dict:
    return {"type": "route", "route": intent}


def verdict(value: Verdict) -> dict:
    return {"type": "verdict", "verdict": value}


def citations(items: list[Citation]) -> dict:
    return {
        "type": "citations",
        "citations": [_json_safe(item.to_dict()) for item in items],
    }


def sql(query: str, *, verified: bool, problems: list[str]) -> dict:
    """Reserved for the verified-statistics phase.

    Included now so the event contract is complete and the UI's SQL panel has
    a stable shape to bind against.
    """
    return {
        "type": "sql",
        "sql": {"sql": query, "verified": verified, "problems": problems},
    }


def result(columns: list[str], rows: list[list[Any]], weight_variable: str) -> dict:
    return {
        "type": "result",
        "result": {
            "columns": columns,
            "rows": _json_safe(rows),
            "weight_variable": weight_variable,
        },
    }


def token(text: str) -> dict:
    return {"type": "token", "text": text}


def error(message: str) -> dict:
    return {"type": "error", "message": message}


def done(message_id: int | None = None) -> dict:
    """Terminator. `message_id` is additive and may be null.

    The frontend's union carries no payload on `done`; sending an optional id
    lets a later feedback endpoint reference the turn without breaking the
    existing reader, which ignores unknown fields.
    """
    return {"type": "done", "message_id": message_id}
