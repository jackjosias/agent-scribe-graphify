from __future__ import annotations

import json as std_json
import math
from typing import Any

import export_public_share_exhaustive as exporter

ORIGINAL_DUMPS = std_json.dumps


def cycle_safe(value: Any, path: str = "$", seen: dict[int, str] | None = None) -> Any:
    if seen is None:
        seen = {}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, dict):
        object_id = id(value)
        if object_id in seen:
            return {"$ref": seen[object_id]}
        seen[object_id] = path
        return {
            str(key): cycle_safe(child, f"{path}.{key}", seen)
            for key, child in value.items()
        }
    if isinstance(value, list):
        object_id = id(value)
        if object_id in seen:
            return {"$ref": seen[object_id]}
        seen[object_id] = path
        return [
            cycle_safe(child, f"{path}[{index}]", seen)
            for index, child in enumerate(value)
        ]
    return repr(value)


def safe_dumps(value: Any, *args: Any, **kwargs: Any) -> str:
    try:
        return ORIGINAL_DUMPS(value, *args, **kwargs)
    except ValueError as exc:
        if "Circular reference" not in str(exc):
            raise
        return ORIGINAL_DUMPS(cycle_safe(value), *args, **kwargs)


exporter.json.dumps = safe_dumps
exporter.main()
