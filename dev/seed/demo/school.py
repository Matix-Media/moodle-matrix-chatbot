"""The fictional world every course shares: school, class, teachers, dates.

All names, addresses and companies are invented. Domains use ``.example`` (RFC 2606).
The school year is 2026/27; the dataset is written as of mid-November 2026, so "next
test" questions have real answers in the near future.
"""

SCHOOL = "Berufliche Schule Elbmarsch"
SCHOOL_SHORT = "BS Elbmarsch"
CLASS = "FIAE 25a"
CLASS_LONG = "Fachinformatiker/-innen Anwendungsentwicklung, Jahrgang 2025, Klasse a"
YEAR = "Schuljahr 2026/27"
HEADER = f"{SCHOOL} · {CLASS} · {YEAR}"

COMPANY = "Nordlicht Logistik GmbH"
PROJECT = "NLV – Nordlicht Lagerverwaltung"

TEACHERS = {
    "ALB": ("Dr. Henrike Albers", "LF5 Datenbanken, Klassenlehrerin", "h.albers"),
    "PET": ("Jonas Petersen", "LF3 Netzwerke", "j.petersen"),
    "DEM": ("Ayşe Demir", "Wirtschafts- und Sozialkunde", "a.demir"),
    "WHI": ("Callum Whitfield", "Englisch", "c.whitfield"),
    "BRA": ("Malte Brandt", "LF8 Schnittstellen, Abteilungsleitung IT", "m.brandt"),
    "KRÜ": ("Kerstin Krüger", "Sprache & Kommunikation", "k.krueger"),
}


def teacher(code: str) -> str:
    return TEACHERS[code][0]


def mail(code: str) -> str:
    return f"{TEACHERS[code][2]}@bs-elbmarsch.example"


# Dates of the first half-year (all of them appear in several documents).
DATES = {
    "lf3_test": "Mittwoch, 25.11.2026",
    "en_vocab": "Mittwoch, 25.11.2026",
    "sprechtag": "Donnerstag, 03.12.2026, 16:00–19:00 Uhr",
    "lf5_klausur": "Montag, 07.12.2026",
    "besichtigung": "Donnerstag, 10.12.2026",
    "wiso_klausur": "Montag, 14.12.2026",
    "weihnachtsferien": "21.12.2026 bis 01.01.2027",
    "lf8_praesentation": "Mittwoch, 20.01.2027",
    "projektabgabe": "Freitag, 22.01.2027, 23:59 Uhr",
    "zeugniskonferenz": "Mittwoch, 27.01.2027",
    "zeugnisse": "Freitag, 29.01.2027",
    "ap1": "Dienstag, 02.03.2027",
}

# Berufsschultage: Montag und Mittwoch.
TIMETABLE = [
    ["Stunde", "Zeit", "Montag", "Raum Mo", "Mittwoch", "Raum Mi"],
    ["1", "07:45–08:30", "LF5 Datenbanken (ALB)", "2.14", "Englisch (WHI)", "1.07"],
    ["2", "08:30–09:15", "LF5 Datenbanken (ALB)", "2.14", "Englisch (WHI)", "1.07"],
    ["3", "09:35–10:20", "LF3 Netzwerke (PET)", "Labor 0.11", "LF8 Schnittstellen (BRA)", "2.16"],
    ["4", "10:20–11:05", "LF3 Netzwerke (PET)", "Labor 0.11", "LF8 Schnittstellen (BRA)", "2.16"],
    ["5", "11:25–12:10", "WiSo (DEM)", "1.03", "LF5 Datenbanken (ALB)", "2.14"],
    ["6", "12:10–12:55", "WiSo (DEM)", "1.03", "LF5 Datenbanken (ALB)", "2.14"],
    [
        "7",
        "13:25–14:10",
        "Sprache & Kommunikation (KRÜ)",
        "1.05",
        "LF3 Netzwerke (PET)",
        "Labor 0.11",
    ],
    ["8", "14:10–14:55", "Sport (wechselnd)", "Halle B", "Förderstunde / Projektzeit", "2.14"],
]
