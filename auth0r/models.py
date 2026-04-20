
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def json_dumps(value: Any) -> str:
    def _default(obj: Any):
        if is_dataclass(obj):
            return asdict(obj)
        raise TypeError(f"Unsupported type: {type(obj)!r}")
    return json.dumps(value, ensure_ascii=False, default=_default, indent=2)
