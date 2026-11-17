"""WiSo: training contract, labour law, collective agreements, social insurance."""

from __future__ import annotations

from .. import images, render
from ..external import URLS
from ..model import Dataset
from ..school import CLASS, COMPANY, DATES, HEADER, teacher

AUTHOR = teacher("DEM")

AUSBILDUNG = f"""
# 1. Das duale System

Die Ausbildung findet an zwei Lernorten statt: im **Ausbildungsbetrieb** (Grundlage:
Ausbildungsvertrag und Ausbildungsordnung) und in der **Berufsschule** (Grundlage:
Rahmenlehrplan und Schulgesetz des Landes). Zuständige Stelle für Fachinformatikerinnen
und Fachinformatiker ist die Industrie- und Handelskammer (IHK).

# 2. Der Ausbildungsvertrag (§§ 10–11 BBiG)

Der Vertrag ist vor Beginn der Ausbildung schriftlich (seit 2024 auch elektronisch in
Textform) abzuschließen. Er muss mindestens enthalten:

- Art, sachliche und zeitliche Gliederung sowie Ziel der Ausbildung
- Beginn und Dauer (Fachinformatiker/-in: 36 Monate)
- Ausbildungsmaßnahmen außerhalb der Ausbildungsstätte
- Dauer der regelmäßigen täglichen Ausbildungszeit
- Dauer der **Probezeit** (mindestens 1, höchstens 4 Monate)
- Zahlung und Höhe der Vergütung
- Dauer des Urlaubs
- Voraussetzungen der Kündigung

# 3. Pflichten

| Pflichten der Auszubildenden (§ 13 BBiG) | Pflichten der Ausbildenden (§ 14 BBiG) |
|---|---|
| Lernpflicht: sich bemühen, die Handlungsfähigkeit zu erwerben | Ausbildungspflicht: Fertigkeiten planmäßig vermitteln |
| Teilnahme am Berufsschulunterricht und an Prüfungen | Freistellung für Berufsschule und Prüfungen (§ 15) |
| Weisungen befolgen | Ausbildungsmittel kostenlos zur Verfügung stellen |
| Betriebsgeheimnisse wahren | Zum Führen des Ausbildungsnachweises anhalten |
| Ausbildungsnachweis führen | Nur ausbildungsbezogene Aufgaben übertragen |

---

# 4. Kündigung (§ 22 BBiG)

- In der **Probezeit**: jederzeit ohne Einhaltung einer Frist und ohne Angabe von Gründen.
- Nach der Probezeit durch den Betrieb nur aus **wichtigem Grund** (fristlos, innerhalb von
  zwei Wochen nach Bekanntwerden).
- Nach der Probezeit durch Auszubildende mit einer Frist von **vier Wochen**, wenn sie die
  Ausbildung aufgeben oder einen anderen Beruf erlernen wollen.
- Die Kündigung muss immer schriftlich erfolgen, nach der Probezeit mit Begründung.

# 5. Vergütung

Die Mindestausbildungsvergütung (§ 17 BBiG) beträgt für Ausbildungen, die 2026 beginnen,
im ersten Jahr 724 Euro. Gilt ein Tarifvertrag, ist dessen Vergütung maßgeblich. Bei
{COMPANY} gilt der Tarifvertrag für die Speditions- und Logistikbranche Hamburg.

# 6. Fallbeispiel

Emre (19) ist seit dem 01.08.2025 Auszubildender bei Nordlicht. Am Mittwoch hatte er sieben
Stunden Berufsschule. Sein Ausbilder verlangt, dass er anschließend noch bis 18 Uhr im
Lager hilft. Prüfen Sie mit § 15 BBiG, ob das zulässig ist.
"""

TARIF = """
# Auszug: Tarifvertrag Ausbildungsvergütung Logistik Hamburg (Lehrbeispiel)

Gültig vom 01.07.2026 bis 30.06.2028. **Hinweis:** vereinfachte Fassung für den
Unterricht, die Beträge sind Beispielwerte.

| Ausbildungsjahr | Vergütung brutto/Monat |
|---|---|
| 1. Jahr | 1.085 € |
| 2. Jahr | 1.160 € |
| 3. Jahr | 1.245 € |

- Urlaub: 30 Arbeitstage pro Kalenderjahr
- Wöchentliche Ausbildungszeit: 38,5 Stunden
- Jahressonderzahlung: 50 % der Monatsvergütung, fällig mit der Novembervergütung
- Vermögenswirksame Leistungen: 26,59 € monatlich

# Aufgaben

1. Unterscheiden Sie Flächentarifvertrag und Haustarifvertrag.
2. Was bedeutet Friedenspflicht?
3. Berechnen Sie die Jahresbruttovergütung im 2. Ausbildungsjahr einschließlich
   Sonderzahlung.
"""

SOZIAL_FOLIEN = """
## Die fünf Säulen der Sozialversicherung
- Krankenversicherung
- Pflegeversicherung
- Rentenversicherung
- Arbeitslosenversicherung
- Unfallversicherung (zahlt allein der Arbeitgeber)

## Beitragssätze 2026 (Lehrbeispiel)
| Zweig | Satz gesamt | Arbeitnehmeranteil |
|---|---|---|
| Krankenversicherung | 14,6 % + Zusatzbeitrag 2,9 % | 8,75 % |
| Pflegeversicherung | 3,6 % | 1,8 % (Kinderlose ab 23: 2,4 %) |
| Rentenversicherung | 18,6 % | 9,3 % |
| Arbeitslosenversicherung | 2,6 % | 1,3 % |
Notiz: Zahlen im Unterricht als Lehrbeispiel kennzeichnen, die aktuellen Sätze jedes Jahr prüfen.

## Geringverdienergrenze
- Bis 325 € Ausbildungsvergütung trägt der Arbeitgeber alle Beiträge allein
- Betrifft bei uns niemanden, alle liegen darüber

## Wer ist zuständig?
- Unfallversicherung: Berufsgenossenschaft (bei Nordlicht: BG Verkehr)
- Wegeunfall auf dem Weg zur Berufsschule ist versichert!
"""

ZEITUNG = """
# Fachkräftemangel: Hamburger Logistiker bilden mehr IT-Nachwuchs aus

HAMBURG. Die Logistikbranche im Hafen sucht händeringend nach IT-Fachkräften. Nach einer
Umfrage der Handelskammer wollen 41 Prozent der befragten Unternehmen im kommenden Jahr
mehr Ausbildungsplätze für Fachinformatikerinnen und Fachinformatiker anbieten.

„Ohne eigene Leute für Lagerverwaltung, Scanner und Schnittstellen zu den Reedereien
können wir die Digitalisierung nicht stemmen“, sagt Sven Lüdemann, IT-Leiter eines
Logistikunternehmens in Billbrook. Sein Betrieb bildet inzwischen sechs Azubis aus,
doppelt so viele wie vor drei Jahren.

Die Gewerkschaft verweist auf die Tarifbindung: Wer tariflich zahlt, findet leichter
Nachwuchs. Die Ausbildungsvergütung im zweiten Jahr liege in tarifgebundenen Betrieben
im Schnitt um 180 Euro höher.

(Aus: Elbmarsch-Kurier, 04.11.2026, Wirtschaftsteil S. 7)
"""


def build(ds: Dataset) -> None:
    c = ds.course(
        "wiso",
        "Wirtschafts- und Sozialkunde FIAE 25a",
        {"teacher": "editingteacher", "student": "student"},
    )

    s = c.section("Allgemeines")
    s.page(
        "info",
        "Kursinfo WiSo",
        f"""
Lehrkraft: {AUTHOR}, montags 5.–6. Stunde, Raum 1.03.

Themen im 1. Halbjahr: Duale Ausbildung und Ausbildungsvertrag, Arbeitsschutz,
Tarifvertrag und Mitbestimmung, Sozialversicherung. Die Klausur schreiben wir am
**{DATES["wiso_klausur"]}** über die Themen 1 bis 3.

Für die WiSo-Prüfung in Teil 2 der Abschlussprüfung lohnt es sich, alle Arbeitsblätter
aufzuheben.
""",
    )
    s.url(
        "gdoc_glossar",
        "Gemeinsames Glossar (Google Docs)",
        URLS["gdoc_glossar"],
        intro="Jede Person ergänzt mindestens drei Fachbegriffe mit Erklärung.",
    )

    s = c.section("Thema 1: Ausbildung und Ausbildungsvertrag")
    s.resource(
        "skript",
        "Skript: Berufsausbildung nach dem BBiG",
        "WiSo_Skript_BBiG.pdf",
        render.to_pdf(
            "Berufsausbildung nach dem Berufsbildungsgesetz",
            AUSBILDUNG,
            header=f"{HEADER} · WiSo",
            author=AUTHOR,
        ),
    )
    s.resource(
        "fall",
        "Fallbeispiele Ausbildungsrecht",
        "Fallbeispiele_Ausbildung.odt",
        render.to_odt(
            "Fallbeispiele zum Ausbildungsrecht",
            """
## Fall 1: Kündigung in der Probezeit

Lina hat ihre Ausbildung am 01.08. begonnen, die Probezeit beträgt vier Monate. Am 20.11.
erhält sie eine schriftliche Kündigung ohne Begründung. Ist die Kündigung wirksam?

## Fall 2: Ausbildungsfremde Tätigkeiten

Jonas soll jeden Freitag das Firmenfahrzeug waschen und Kaffee für Besprechungen kochen.
Beurteilen Sie die Lage mit § 14 BBiG.

## Fall 3: Berufsschultag

Nach einem Berufsschultag mit sechs Unterrichtsstunden à 45 Minuten soll Mara (17) noch in
den Betrieb kommen. Was regelt das Jugendarbeitsschutzgesetz (§ 9 JArbSchG)?
""",
            author=AUTHOR,
        ),
    )
    s.url(
        "gslides",
        "Präsentation der Gruppen (Google Slides)",
        URLS["gslides_wiso"],
        intro="Die Ergebnisse der Gruppenarbeit vom 09.11.2026.",
    )

    s = c.section("Thema 2: Tarifvertrag und Mitbestimmung")
    s.resource(
        "tarif",
        "Tarifvertrag Auszug (Lehrbeispiel)",
        "Tarifvertrag_Logistik_Auszug.docx",
        render.to_docx("Tarifvertrag Ausbildungsvergütung (Auszug)", TARIF, author=AUTHOR),
    )
    s.resource(
        "zeitung",
        "Zeitungsartikel Fachkräftemangel (Scan)",
        "Zeitungsartikel_2026-11-04.pdf",
        render.to_scanned_pdf(
            "Wirtschaft", ZEITUNG, seed=7, header="Elbmarsch-Kurier · Mittwoch, 4. November 2026"
        ),
    )
    s.url(
        "gsheet",
        "Rechner Ausbildungsvergütung (Google Sheets)",
        URLS["gsheet_verguetung"],
        intro="Ausbildungsjahr in B2 eintragen. Nur Lesezugriff: Datei → Kopie erstellen.",
    )
    s.resource(
        "aushang",
        "Foto: Aushang JAV-Wahl bei Nordlicht",
        "Aushang_JAV_Wahl.jpeg",
        images.whiteboard_photo(
            [
                "Wahl der Jugend- und Auszubildendenvertretung",
                "Nordlicht Logistik GmbH - Standort Billbrook",
                "Wahltag: Donnerstag, 26.11.2026, 09:00-14:00 Uhr",
                "Wahllokal: Kantine Halle 2",
                "Wahlberechtigt: alle Azubis und alle unter 18",
                "Kandidaten: E. Kaya, J. Wolff, L. Brockmann",
                "Briefwahl bis 24.11. beim Wahlvorstand",
            ],
            seed=26,
        ),
        intro="Von einer Mitschülerin fotografiert. Wer darf wählen? Wie viele JAV-Mitglieder "
        "hat ein Betrieb mit 6 Azubis (§ 62 BetrVG)?",
    )
    s.activity(
        "feedback",
        "umfrage",
        "Umfrage: Tarifbindung in euren Betrieben",
        """
Anonyme Umfrage: Gilt in Ihrem Ausbildungsbetrieb ein Tarifvertrag? Gibt es einen
Betriebsrat oder eine Jugend- und Auszubildendenvertretung (JAV)? Die Ergebnisse
besprechen wir am 23.11.2026.
""",
    )

    s = c.section("Thema 3: Sozialversicherung")
    s.resource(
        "sozial",
        "Folien Sozialversicherung",
        "WiSo_Sozialversicherung.pptx",
        render.to_pptx(
            "Die Sozialversicherung", SOZIAL_FOLIEN, subtitle=f"WiSo · {CLASS} · {AUTHOR}"
        ),
    )
    s.resource(
        "abrechnung",
        "Beispiel-Gehaltsabrechnung",
        "Beispiel_Abrechnung_2.Jahr.xlsx",
        render.to_xlsx(
            [
                (
                    "Abrechnung November",
                    [
                        ["Position", "Betrag €", "Bemerkung"],
                        ["Ausbildungsvergütung brutto", 1160.00, "2. Ausbildungsjahr laut Tarif"],
                        ["Vermögenswirksame Leistungen", 26.59, ""],
                        ["Jahressonderzahlung", 580.00, "50 % im November"],
                        ["Gesamtbrutto", 1766.59, ""],
                        ["Lohnsteuer", 112.25, "Steuerklasse I (Beispiel)"],
                        ["Krankenversicherung AN", 154.58, "8,75 %"],
                        ["Pflegeversicherung AN", 31.80, "1,8 %"],
                        ["Rentenversicherung AN", 164.29, "9,3 %"],
                        ["Arbeitslosenversicherung AN", 22.97, "1,3 %"],
                        ["Netto", 1280.70, ""],
                        ["Überweisung VL-Vertrag", 40.00, "Bausparvertrag"],
                        ["Auszahlung", 1240.70, ""],
                    ],
                )
            ],
            title="Beispiel-Gehaltsabrechnung",
        ),
        intro="Alle Werte sind Lehrbeispiele und gerundet.",
    )

    ds.probe(
        "Wie lange darf die Probezeit in der Ausbildung sein?",
        "1 bis 4 Monate",
        "WiSo_Skript_BBiG.pdf",
    )
    ds.probe(
        "Wie viel verdient man im 2. Ausbildungsjahr laut Tarifvertrag?",
        "1.160 € brutto",
        "Tarifvertrag_Logistik_Auszug.docx, Beispiel_Abrechnung_2.Jahr.xlsx",
    )
    ds.probe(
        "Wann ist die JAV-Wahl bei Nordlicht?",
        "Donnerstag, 26.11.2026, 09:00–14:00 Uhr in der Kantine Halle 2 (nur per OCR)",
        "Aushang_JAV_Wahl.jpeg",
    )
    ds.probe(
        "Wie viele Unternehmen wollen laut Zeitungsartikel mehr ausbilden?",
        "41 Prozent (nur per OCR)",
        "Zeitungsartikel_2026-11-04.pdf (Scan)",
    )
    ds.probe(
        "Was ist ein Manteltarifvertrag?",
        "regelt allgemeine Arbeitsbedingungen (Arbeitszeit, Urlaub, Kündigungsfristen); "
        "die Vergütung steht im Entgelttarifvertrag",
        "Gemeinsames Glossar (Google Docs, extern)",
        "student, teacher",
    )
    ds.probe(
        "Was hat die Gruppe 4 zur Kündigung herausgearbeitet?",
        "Probezeit: jederzeit ohne Frist, schriftlich; danach nur aus wichtigem Grund; "
        "Lina (Fall 1) wirksam gekündigt",
        "Präsentation der Gruppen (Google Slides, extern)",
        "student, teacher",
    )
    ds.probe(
        "Wie viel netto bleibt im 2. Ausbildungsjahr?",
        "935,62 € (laut Rechner, ohne Sonderzahlung; Beispielabrechnung November: 1.280,70 €)",
        "Rechner Ausbildungsvergütung (Google Sheets, extern), Beispiel_Abrechnung_2.Jahr.xlsx",
        "student, teacher",
    )
