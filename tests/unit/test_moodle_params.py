"""Verifies spec 002 AC-7 — Moodle's PHP-array parameter encoding.

Moodle does not accept JSON bodies for REST calls; it expects PHP's bracketed
form-encoding. Getting this wrong produces silent empty results rather than an
error, which is why it gets its own test module.
"""

from __future__ import annotations

import pytest

from bsbot.moodle.params import encode_params


def test_scalars_pass_through() -> None:
    assert encode_params({"courseid": 2, "name": "LF5"}) == {"courseid": "2", "name": "LF5"}


def test_booleans_become_one_and_zero() -> None:
    """PHP truthiness: Moodle expects 1/0, not 'True'/'False'."""
    assert encode_params({"a": True, "b": False}) == {"a": "1", "b": "0"}


def test_none_values_are_dropped() -> None:
    """An omitted optional parameter must not be sent as the string 'None'."""
    assert encode_params({"courseid": 2, "since": None}) == {"courseid": "2"}


def test_list_of_scalars_is_indexed() -> None:
    assert encode_params({"courseids": [2, 5]}) == {"courseids[0]": "2", "courseids[1]": "5"}


def test_list_of_dicts_is_nested() -> None:
    """The options[] pattern used by most core_* functions."""
    encoded = encode_params(
        {"options": [{"name": "excludemodules", "value": "0"}, {"name": "sectionid", "value": "3"}]}
    )
    assert encoded == {
        "options[0][name]": "excludemodules",
        "options[0][value]": "0",
        "options[1][name]": "sectionid",
        "options[1][value]": "3",
    }


def test_nested_dict_is_bracketed() -> None:
    assert encode_params({"filters": {"lang": "de"}}) == {"filters[lang]": "de"}


def test_deeply_nested_structure() -> None:
    encoded = encode_params({"a": [{"b": [{"c": 1}]}]})
    assert encoded == {"a[0][b][0][c]": "1"}


def test_empty_list_produces_nothing() -> None:
    assert encode_params({"courseids": []}) == {}


def test_unsupported_type_is_rejected_loudly() -> None:
    """Silently stringifying an object would produce a confusing empty result."""
    with pytest.raises(TypeError):
        encode_params({"x": object()})
