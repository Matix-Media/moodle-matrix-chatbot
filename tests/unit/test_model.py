"""Verifies spec 003/006 — file classification (``classify_file``).

Regression coverage for a real gap found while wiring image OCR support: removing
"image/" from BINARY_MIME_PREFIXES entirely (to let PNG/JPEG through) would have
also waved through *other* image types we have no extractor for, whenever a file
arrives with a mimetype but no recognisable extension.
"""

from __future__ import annotations

from bsbot.ingest.model import FileRef, classify_file

MAX_BYTES = 25 * 1024 * 1024


def ref(**kw) -> FileRef:
    defaults = dict(url="https://m.example/f", filename="f", filesize=1000, mimetype=None)
    return FileRef(**{**defaults, **kw})


class TestOcrImages:
    def test_png_by_extension_is_extractable(self) -> None:
        extractable, reason = classify_file(ref(filename="foto.png"), MAX_BYTES)
        assert extractable is True and reason is None

    def test_jpeg_by_extension_is_extractable(self) -> None:
        extractable, reason = classify_file(ref(filename="foto.jpeg"), MAX_BYTES)
        assert extractable is True and reason is None

    def test_png_by_mimetype_with_no_extension_is_extractable(self) -> None:
        extractable, reason = classify_file(
            ref(filename="attachment", mimetype="image/png"), MAX_BYTES
        )
        assert extractable is True and reason is None

    def test_oversized_image_is_still_excluded(self) -> None:
        """The size cap must still apply to images, same as any other file."""
        extractable, reason = classify_file(
            ref(filename="riesig.png", filesize=MAX_BYTES + 1), MAX_BYTES
        )
        assert extractable is False and reason == "too-large"


class TestOtherImageTypesStillExcluded:
    """Regression: the carve-out for PNG/JPEG must not leak to other image types."""

    def test_gif_by_extension_is_still_binary(self) -> None:
        """WEBP was added deliberately (spec 006 AC-24); GIF stays the control
        case proving the carve-out did not leak to formats we still don't support."""
        extractable, reason = classify_file(ref(filename="anim.gif"), MAX_BYTES)
        assert extractable is False and reason == "binary-type"

    def test_extensionless_tiff_by_mimetype_is_still_binary(self) -> None:
        """The exact gap found during implementation: an extensionless file whose
        mimetype merely starts with 'image/' must not fall through to extractable
        just because PNG/JPEG/WEBP are exempted."""
        extractable, reason = classify_file(
            ref(filename="attachment", mimetype="image/tiff"), MAX_BYTES
        )
        assert extractable is False and reason == "binary-type"


class TestVideoAudioUnaffected:
    def test_video_is_still_binary(self) -> None:
        extractable, reason = classify_file(ref(filename="clip.mp4"), MAX_BYTES)
        assert extractable is False and reason == "binary-type"

    def test_audio_by_mimetype_is_still_binary(self) -> None:
        extractable, reason = classify_file(
            ref(filename="attachment", mimetype="audio/mpeg"), MAX_BYTES
        )
        assert extractable is False and reason == "binary-type"


class TestWebp:
    def test_webp_by_extension_is_extractable(self) -> None:
        extractable, reason = classify_file(ref(filename="foto.webp"), MAX_BYTES)
        assert extractable is True and reason is None

    def test_webp_by_mimetype_with_no_extension_is_extractable(self) -> None:
        extractable, reason = classify_file(
            ref(filename="attachment", mimetype="image/webp"), MAX_BYTES
        )
        assert extractable is True and reason is None
