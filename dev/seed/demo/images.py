"""Raster images with readable text in them: diagrams, a whiteboard photo, a screenshot.

Every image carries real words, so the API's OCR (and a vision model) has something to
find. Drawn with Pillow's bundled font, so the output is the same on every machine.
"""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


_ASCII = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "–": "-"}
)


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    fill: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    """Pillow's bundled font has no umlauts: write them the way a whiteboard would."""
    draw.text(xy, text.translate(_ASCII), fill=fill, font=font)


def _save(image: Image.Image, fmt: str) -> bytes:
    buffer = io.BytesIO()
    if fmt == "JPEG":
        image.convert("RGB").save(buffer, "JPEG", quality=82)
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()


def _box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    lines: list[str],
    *,
    fill: str = "#f1f5fb",
) -> None:
    x0, y0, x1, _ = xy
    draw.rectangle(xy, fill=fill, outline="#1d3557", width=3)
    draw.rectangle((x0, y0, x1, y0 + 38), fill="#1d3557")
    _text(draw, (x0 + 10, y0 + 8), title, fill="white", font=_font(22))
    for n, line in enumerate(lines):
        _text(draw, (x0 + 12, y0 + 50 + n * 28), line, fill="#111", font=_font(18))


def er_diagram() -> bytes:
    """ERM of the Nordlicht warehouse database (Crow's foot labels as text)."""
    image = Image.new("RGB", (1400, 820), "white")
    d = ImageDraw.Draw(image)
    _text(
        d,
        (30, 20),
        "ERM Lagerverwaltung Nordlicht Logistik GmbH (Entwurf v3, Albers)",
        fill="#111",
        font=_font(26),
    )
    _box(
        d,
        (40, 100, 380, 330),
        "Kunde",
        ["PK kunde_id", "firmenname", "ansprechpartner", "plz, ort", "kundenseit"],
    )
    _box(
        d,
        (530, 100, 870, 360),
        "Auftrag",
        ["PK auftrag_id", "FK kunde_id", "auftragsdatum", "lieferdatum", "status", "FK lager_id"],
    )
    _box(
        d,
        (1020, 100, 1360, 330),
        "Artikel",
        ["PK artikel_nr", "bezeichnung", "gewicht_kg", "einzelpreis", "FK kategorie_id"],
    )
    _box(
        d,
        (530, 480, 870, 700),
        "Auftragsposition",
        ["PK,FK auftrag_id", "PK,FK artikel_nr", "menge", "rabatt_prozent"],
    )
    _box(
        d,
        (40, 480, 380, 700),
        "Lager",
        ["PK lager_id", "standort", "flaeche_qm", "leiter (FK mitarbeiter)"],
    )
    _box(
        d,
        (1020, 480, 1360, 700),
        "Lagerbestand",
        ["PK,FK lager_id", "PK,FK artikel_nr", "bestand", "mindestbestand"],
    )
    for (a, b), label in {
        ((380, 210), (530, 210)): "1 : n  erteilt",
        ((700, 360), (700, 480)): "1 : n  enthält",
        ((1190, 330), (870, 590)): "1 : n  wird bestellt",
        ((380, 590), (530, 300)): "1 : n  liefert aus",
        ((380, 640), (1020, 640)): "m : n  lagert (über Lagerbestand)",
    }.items():
        d.line((a, b), fill="#e63946", width=3)
        _text(
            d,
            ((a[0] + b[0]) // 2 + 8, (a[1] + b[1]) // 2 - 26),
            label,
            fill="#e63946",
            font=_font(18),
        )
    return _save(image, "PNG")


def network_plan() -> bytes:
    """Network plan for the new Nordlicht warehouse hall (LF3 project)."""
    image = Image.new("RGB", (1500, 900), "#fbfbf8")
    d = ImageDraw.Draw(image)
    _text(
        d,
        (30, 20),
        "Netzplan Halle 4 - Nordlicht Logistik, Standort Billbrook",
        fill="#111",
        font=_font(28),
    )
    _box(
        d,
        (620, 80, 900, 210),
        "Router / Firewall",
        ["WAN: 203.0.113.18/29", "LAN: 10.40.0.1"],
        fill="#fff4e6",
    )
    _box(d, (620, 290, 900, 420), "Core-Switch SW-H4-01", ["24x 1G, 4x SFP+", "VLAN 10/20/30/99"])
    segments = [
        (
            (40, 540, 380, 820),
            "VLAN 10 Büro",
            ["10.40.10.0/26", "GW 10.40.10.1", "18 PCs, 2 Drucker"],
        ),
        (
            (420, 540, 760, 820),
            "VLAN 20 Lager",
            ["10.40.20.0/25", "GW 10.40.20.1", "60 Handscanner (WLAN)"],
        ),
        (
            (800, 540, 1140, 820),
            "VLAN 30 Kameras",
            ["10.40.30.0/27", "GW 10.40.30.1", "24 IP-Kameras (PoE)"],
        ),
        ((1180, 540, 1470, 820), "VLAN 99 Mgmt", ["10.40.99.0/28", "Switches, APs, USV"]),
    ]
    for xy, title, lines in segments:
        _box(d, xy, title, lines)
        d.line(((xy[0] + xy[2]) // 2, xy[1], 760, 420), fill="#457b9d", width=3)
    d.line((760, 210, 760, 290), fill="#457b9d", width=5)
    _text(
        d,
        (30, 860),
        "Hinweis: Adressplan siehe 'Subnetting-Skript', Aufgabe 6. Stand 03.11.2026",
        fill="#555",
        font=_font(18),
    )
    return _save(image, "PNG")


def whiteboard_photo(lines: list[str], *, seed: int) -> bytes:
    """A slightly skewed, unevenly lit photo of a whiteboard."""
    rng = random.Random(seed)
    image = Image.new("RGB", (1600, 1100), "#eceeea")
    d = ImageDraw.Draw(image)
    for n, line in enumerate(lines):
        colour = rng.choice(["#1a3d8f", "#1a3d8f", "#b3261e", "#1b1b1b"])
        _text(d, (70 + rng.randint(-8, 8), 70 + n * 72), line, fill=colour, font=_font(44))
    image = image.rotate(rng.uniform(-3, 3), fillcolor="#7a7f78", expand=True)
    shade = Image.linear_gradient("L").resize(image.size).point(lambda v: 255 - v // 5)
    image = Image.composite(image, Image.new("RGB", image.size, "#9da39a"), shade)
    image = image.filter(ImageFilter.GaussianBlur(1.1))
    return _save(image, "JPEG")


def screenshot(title: str, rows: list[tuple[str, str]]) -> bytes:
    """A screenshot of a settings dialog, saved as WEBP."""
    image = Image.new("RGB", (1100, 120 + 46 * len(rows)), "#ffffff")
    d = ImageDraw.Draw(image)
    d.rectangle((0, 0, 1100, 56), fill="#2b2d42")
    _text(d, (20, 14), title, fill="white", font=_font(24))
    for n, (key, value) in enumerate(rows):
        y = 80 + n * 46
        _text(d, (30, y), key, fill="#333", font=_font(20))
        d.rectangle((420, y - 6, 1060, y + 30), outline="#999", width=2)
        _text(d, (432, y), value, fill="#111", font=_font(20))
    return _save(image, "WEBP")


def logo() -> bytes:
    """Small decorative school logo (no meaningful text beyond the name)."""
    image = Image.new("RGB", (480, 140), "white")
    d = ImageDraw.Draw(image)
    d.ellipse((10, 10, 130, 130), fill="#1d3557")
    _text(d, (42, 44), "BSE", fill="white", font=_font(34))
    _text(d, (150, 36), "Berufliche Schule", fill="#1d3557", font=_font(28))
    _text(d, (150, 76), "Elbmarsch", fill="#e63946", font=_font(34))
    return _save(image, "PNG")
