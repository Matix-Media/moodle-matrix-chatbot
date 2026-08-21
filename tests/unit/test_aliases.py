"""Verifies spec 007 AC-19..AC-22 — institutional-knowledge aliases.

Some Moodle documents are named after context with no lexical or semantic
connection to what a student would search for — a Lernfeld 10 grading sheet named
"Bewertung Barcamp" because the LF10 project is presented at an event called a
Barcamp. No retrieval mechanism discovers that from the text; a human has to say it
once, in this file, the same way a teacher eventually tells a student who asks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bsbot.index.aliases import alias_text, load_aliases


class TestLoading:
    def test_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        """AC-21: most deployments will not have this file at all."""
        assert load_aliases(tmp_path / "nope.yaml") == {}

    def test_valid_file_is_parsed(self, tmp_path: Path) -> None:
        """AC-19"""
        path = tmp_path / "aliases.yaml"
        path.write_text(
            "'2250:170434:0':\n  - Bewertungsbogen Lernfeld 10\n  - Bewertungskriterien LF10\n"
        )
        result = load_aliases(path)
        assert result == {
            "2250:170434:0": ["Bewertungsbogen Lernfeld 10", "Bewertungskriterien LF10"]
        }

    def test_empty_file_is_an_empty_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "aliases.yaml"
        path.write_text("")
        assert load_aliases(path) == {}

    def test_non_mapping_top_level_is_rejected(self, tmp_path: Path) -> None:
        """A human editing YAML by hand will eventually get the shape wrong."""
        path = tmp_path / "aliases.yaml"
        path.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="mapping"):
            load_aliases(path)

    def test_non_list_value_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "aliases.yaml"
        path.write_text("'2250:170434:0': not a list\n")
        with pytest.raises(ValueError, match="list"):
            load_aliases(path)

    def test_doc_ids_and_aliases_are_coerced_to_strings(self, tmp_path: Path) -> None:
        """YAML parses '10' as an int unless quoted; a bare number must not crash."""
        path = tmp_path / "aliases.yaml"
        path.write_text("123: [456]\n")
        assert load_aliases(path) == {"123": ["456"]}


class TestAliasText:
    def test_renders_as_one_searchable_line(self) -> None:
        """AC-20: this becomes the document's own retrievable unit."""
        text = alias_text(["Bewertungsbogen Lernfeld 10", "Bewertungskriterien LF10"])
        assert "Bewertungsbogen Lernfeld 10" in text
        assert "Bewertungskriterien LF10" in text
