from __future__ import annotations

import hashlib
import json
from time import monotonic
from typing import Any


def dump_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=True)


def hash_text(value: str) -> str:
    value_buffer = memoryview(value.encode("utf-8"))
    return hashlib.sha256(value_buffer).hexdigest()


def elapsed_ms(started_at: float) -> int:
    return round((monotonic() - started_at) * 1000)
