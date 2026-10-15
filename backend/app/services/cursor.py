"""Opaque keyset cursors: base64url(JSON [iso timestamp, id]). Pure."""

import base64
import json
from datetime import datetime


class BadCursor(ValueError):
    pass


def encode_cursor(ts: datetime, row_id: int) -> str:
    raw = json.dumps([ts.isoformat(), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts_raw, row_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        ts = datetime.fromisoformat(ts_raw)
        if ts.tzinfo is None or not isinstance(row_id, int) or isinstance(row_id, bool):
            raise ValueError
        return ts, row_id
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise BadCursor("invalid cursor") from exc
