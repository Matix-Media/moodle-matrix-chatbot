"""PHP-array parameter encoding for Moodle's REST endpoint (spec 002 AC-7).

Moodle expects PHP's bracketed form encoding::

    {"courseids": [2, 5]}  ->  courseids[0]=2&courseids[1]=5

Sending JSON instead yields an empty result rather than an error, so this is
encoded explicitly and tested on its own.
"""

from __future__ import annotations

from typing import Any


def encode_params(params: dict[str, Any]) -> dict[str, str]:
    """Flatten a nested structure into Moodle's bracketed form encoding."""
    flat: dict[str, str] = {}
    for key, value in params.items():
        _flatten(key, value, flat)
    return flat


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    if value is None:
        # An absent optional parameter must not be sent as the string "None".
        return
    if isinstance(value, bool):
        # Checked before int: bool is a subclass of int, and PHP wants 1/0.
        out[prefix] = "1" if value else "0"
    elif isinstance(value, str | int | float):
        out[prefix] = str(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}[{key}]", item, out)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _flatten(f"{prefix}[{index}]", item, out)
    else:
        raise TypeError(
            f"cannot encode parameter {prefix!r} of type {type(value).__name__} for Moodle"
        )
