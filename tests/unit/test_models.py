"""Validation of what the plugin pushes."""

from __future__ import annotations

import pytest
from factories import doc
from pydantic import ValidationError

from moodle_llm_chat.index.models import DocumentIn


def test_file_document_requires_file_reference() -> None:
    with pytest.raises(ValidationError, match="file reference"):
        doc(kind="file", text=None)


def test_inline_document_requires_text() -> None:
    with pytest.raises(ValidationError, match="needs text"):
        doc(kind="inline", text=None)


def test_external_document_requires_url() -> None:
    with pytest.raises(ValidationError, match="external_url"):
        doc(kind="external", text=None)


def test_external_document_ok() -> None:
    d = doc(kind="external", text=None, external_url="https://youtu.be/abc")
    assert isinstance(d, DocumentIn)


def test_file_sha_must_be_hex_sha256() -> None:
    with pytest.raises(ValidationError):
        DocumentIn.model_validate(
            {
                "doc_id": "1:2:f",
                "course_id": 1,
                "cmid": 2,
                "kind": "file",
                "content_hash": "h",
                "file": {"sha256": "not-a-hash", "filename": "a.pdf"},
            }
        )
