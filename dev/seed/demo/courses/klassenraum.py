"""Klassenraum FIAE 25a: organisation, dates, forms and the cross-subject project."""

from __future__ import annotations

from .. import images, render
from ..external import URLS
from ..model import NEVER, Dataset
from ..school import (
    CLASS,
    COMPANY,
    DATES,
    PROJECT,
    SCHOOL,
    TEACHERS,
    TIMETABLE,
    YEAR,
    mail,
    teacher,
)

TERMINE = f"""
## Leistungsnachweise

| Datum | Fach | Was | Hinweise |
|---|---|---|---|
| {DATES["lf3_test"]} | LF3 Netzwerke | Test Subnetting (45 min) | 3.–4. Stunde, Labor 0.11, Taschenrechner erlaubt |
| {DATES["en_vocab"]} | Englisch | Vocabulary test Unit 3 | 1. Stunde, Raum 1.07 |
| {DATES["lf5_klausur"]} | LF5 Datenbanken | Klausur SQL (90 min) | 1.–2. Stunde, Raum 2.14, ohne Hilfsmittel |
| {DATES["wiso_klausur"]} | WiSo | Klausur Ausbildung & Arbeitsrecht | 5.–6. Stunde, Raum 1.03 |
| {DATES["lf8_praesentation"]} | LF8 Schnittstellen | Projektpräsentationen NLV | 3.–4. Stunde, je Gruppe 12 min |
| {DATES["projektabgabe"]} | Lernfeldprojekt | Abgabe Projektdokumentation | über die Aufgabe „Projektabgabe NLV“ |

## Weitere Termine

| Datum | Termin |
|---|---|
| {DATES["sprechtag"]} | Ausbildersprechtag (Anmeldung über das Sekretariat) |
| {DATES["besichtigung"]} | Betriebsbesichtigung {COMPANY}, Treffpunkt 08:00 Uhr Haupteingang Halle 4 |
| {DATES["weihnachtsferien"]} | Weihnachtsferien – kein Unterricht |
| {DATES["zeugniskonferenz"]} | Zeugniskonferenz, Unterricht endet nach der 4. Stunde |
| {DATES["zeugnisse"]} | Ausgabe der Halbjahreszeugnisse |
| {DATES["ap1"]} | Abschlussprüfung Teil 1 (IHK), Ort laut Einladung |

> Termine können sich ändern. Verbindlich ist immer die Ankündigung im Forum „Ankündigungen“.
"""

SCHULORDNUNG = """
# 1. Geltungsbereich

Diese Schulordnung gilt für alle Schülerinnen und Schüler der Beruflichen Schule Elbmarsch,
für Teilzeit- und Vollzeitklassen gleichermaßen, und auf dem gesamten Schulgelände
einschließlich der Parkplätze und der Sporthalle B.

# 2. Unterrichtszeiten und Pünktlichkeit

Der Unterricht beginnt um 07:45 Uhr. Wer nach Beginn der Stunde kommt, gilt als verspätet;
Verspätungen werden im Klassenbuch vermerkt und dem Ausbildungsbetrieb ab der dritten
Verspätung im Halbjahr mitgeteilt. Die großen Pausen sind von 09:15 bis 09:35 Uhr und
von 11:05 bis 11:25 Uhr, die Mittagspause von 12:55 bis 13:25 Uhr.

# 3. Fehlzeiten und Entschuldigungen

Berufsschulpflicht ist Teil der Ausbildung. Bei Krankheit ist die Schule **am ersten
Fehltag bis 07:30 Uhr** über das Sekretariat (Telefon 040 000 4711-0) oder per E-Mail an
die Klassenleitung zu informieren. Die schriftliche Entschuldigung mit dem Formular
„Entschuldigung Fehlzeiten“ ist innerhalb von **drei Unterrichtstagen** nach der Rückkehr
abzugeben und muss vom Ausbildungsbetrieb gegengezeichnet sein.

Bei Fehlen an einem Tag mit angekündigter Klausur ist **immer ein ärztliches Attest**
vorzulegen; ohne Attest wird die Klausur mit „ungenügend“ bewertet. Nachschreibtermine
legt die Fachlehrkraft fest, in der Regel in der Förderstunde am Mittwoch, 8. Stunde.

Freistellungen (z. B. für betriebliche Schulungen oder Prüfungen) beantragt der Betrieb
mindestens zwei Wochen vorher mit dem Formular „Antrag auf Freistellung“.

---

# 4. Umgang mit IT-Systemen

- Jede Person meldet sich nur mit dem eigenen Konto an. Passwörter werden nicht
  weitergegeben.
- Im Labor 0.11 dürfen Netzwerkkabel nur nach Aufforderung der Lehrkraft umgesteckt
  werden. Die Laborswitches gehören zum Unterrichtsnetz VLAN 200 und sind vom
  Verwaltungsnetz getrennt.
- Private Geräte dürfen das WLAN „BSE-Schueler“ nutzen. Das Scannen fremder Geräte,
  Port-Scans und Mitschnitte außerhalb einer Laborübung sind verboten.
- Generative KI-Werkzeuge dürfen im Unterricht nur genutzt werden, wenn die Lehrkraft das
  für die Aufgabe ausdrücklich erlaubt. In Klausuren sind sie immer verboten.

# 5. Handys und Medien

Im Unterricht bleiben Mobiltelefone stumm und in der Tasche, außer die Lehrkraft erlaubt
die Nutzung. Ton- und Bildaufnahmen von Personen sind ohne deren Einwilligung verboten.

# 6. Ordnungsmaßnahmen

Verstöße werden nach § 49 Hamburgisches Schulgesetz behandelt. Bei wiederholten
unentschuldigten Fehlzeiten informiert die Schule den Ausbildungsbetrieb und die
zuständige Kammer.

Beschlossen von der Schulkonferenz am 12.06.2025, in Kraft seit 01.08.2025.
"""

PROJEKTAUFTRAG = f"""
## Ausgangslage

Die {COMPANY} betreibt in Hamburg-Billbrook drei Lagerhallen. Bestände, Kundenaufträge
und Ein- und Auslagerungen werden heute in einer gewachsenen Excel-Arbeitsmappe gepflegt,
die von bis zu acht Personen gleichzeitig geöffnet wird. Doppelte Artikelnummern,
überschriebene Bestände und fehlende Historie führen regelmäßig zu Fehllieferungen.
Zum 01.03.2027 nimmt Nordlicht außerdem die neue **Halle 4** in Betrieb.

## Auftrag

Entwickeln Sie in Gruppen zu vier Personen einen Prototypen der **{PROJECT}**:

1. ein normalisiertes relationales Datenmodell mit Beispieldaten (Lernfeld 5),
2. eine REST-Schnittstelle, über die Handscanner Ein- und Auslagerungen buchen (Lernfeld 8),
3. einen Netzplan mit Adresskonzept für Halle 4 (Lernfeld 3),
4. eine englische Kurzanleitung für Lagerpersonal und ein Abstract (Englisch).

## Meilensteine

| Meilenstein | Inhalt | Termin |
|---|---|---|
| M1 | ERM und Relationenschema abgenommen | 30.11.2026 |
| M2 | SQL-Skript mit Testdaten lauffähig | 07.12.2026 |
| M3 | API-Spezifikation (OpenAPI) | 16.12.2026 |
| M4 | Prototyp und Netzplan fertig | 18.01.2027 |
| M5 | Präsentation im LF8-Unterricht | {DATES["lf8_praesentation"]} |
| M6 | Abgabe der Projektdokumentation | {DATES["projektabgabe"]} |

## Bewertung

Die Projektnote zählt in LF5, LF8 und LF3 jeweils als eine Klausurnote. Das
Bewertungsraster liegt in diesem Abschnitt. Ansprechpartner im Betrieb ist der
Ausbilder **Sven Lüdemann** (IT-Leitung Nordlicht), der bei der Betriebsbesichtigung am
{DATES["besichtigung"]} Fragen der Gruppen beantwortet.
"""

LASTENHEFT = f"""
# 1. Zielbestimmung

Die {PROJECT} ersetzt die Excel-Mappe „Bestand_Lager_2019_FINAL_neu.xlsx“. Muss-Kriterien
sind mit **M**, Wunsch-Kriterien mit **W** gekennzeichnet.

# 2. Produkteinsatz

Anwender sind Lagerpersonal (Handscanner, ca. 60 Geräte), Disposition (Büro, 18
Arbeitsplätze) und die Lagerleitung. Betrieb im Rechenzentrum der Firma, Zugriff nur aus
dem internen Netz.

# 3. Funktionale Anforderungen

| Nr. | Anforderung | Prio |
|---|---|---|
| F01 | Artikel anlegen, ändern, sperren (nicht löschen) | M |
| F02 | Artikelnummer ist eindeutig, Format NL-#####  (fünf Ziffern) | M |
| F03 | Einlagerung per Scan: Artikel, Lagerplatz, Menge | M |
| F04 | Auslagerung zu einem Auftrag, Bestand darf nicht negativ werden | M |
| F05 | Unterschreitet ein Bestand den Mindestbestand, erscheint der Artikel in der Nachbestellliste | M |
| F06 | Jede Buchung wird mit Zeitpunkt und Benutzer protokolliert | M |
| F07 | Auswertung Umschlag pro Artikel und Monat | W |
| F08 | Export der Nachbestellliste als CSV | W |
| F09 | Mehrere Lager (Halle 1–4) mit eigenem Bestand | M |

---

# 4. Nichtfunktionale Anforderungen

- Antwortzeit einer Buchung unter 1 Sekunde bei 60 gleichzeitigen Scannern.
- Verfügbarkeit Mo–Sa 05:00–22:00 Uhr.
- Datenschutz: personenbezogene Daten nur Name und Personalnummer der Beschäftigten.
- Schnittstelle: JSON über HTTPS, Authentifizierung per API-Schlüssel je Gerät.

# 5. Mengengerüst

Etwa 14.000 Artikel, 900 Kunden, 250 Aufträge pro Tag mit durchschnittlich 6 Positionen,
3.500 Lagerbuchungen pro Tag.

# 6. Abnahme

Die Abnahme erfolgt durch Herrn Lüdemann anhand der Testfälle T01–T12 der
Projektdokumentation.
"""


def build(ds: Dataset) -> None:
    c = ds.course(
        "klasse",
        f"Klassenraum {CLASS} ({YEAR})",
        {"teacher": "editingteacher", "student": "student", "student2": "student"},
    )

    s = c.section("Allgemeines")
    s.label(
        "willkommen",
        f"""
**Willkommen im Klassenraum der {CLASS}!** Hier finden Sie alles, was nicht zu einem
einzelnen Fach gehört: Stundenplan, Termine, Formulare und das Lernfeldprojekt
„{PROJECT}“. Ihre Klassenlehrerin ist {teacher("ALB")}.
""",
    )
    s.activity(
        "forum",
        "news",
        "Ankündigungen",
        """
Wichtige Mitteilungen der Klassenleitung und der Fachlehrkräfte. Bitte abonnieren Sie
das Forum, damit Sie Raumänderungen und Vertretungen per E-Mail erhalten.
""",
    )
    s.page(
        "termine",
        "Termine 1. Halbjahr",
        TERMINE,
        intro="Klausuren, Tests und wichtige Termine bis zu den Halbjahreszeugnissen.",
    )
    s.resource(
        "stundenplan",
        "Stundenplan FIAE 25a (gültig ab 02.11.2026)",
        "Stundenplan_FIAE25a_ab_2026-11-02.xlsx",
        render.to_xlsx(
            [
                ("Stundenplan", [list(r) for r in TIMETABLE]),
                (
                    "Lehrkräfte",
                    [["Kürzel", "Name", "Fach", "E-Mail"]]
                    + [[k, v[0], v[1], mail(k)] for k, v in TEACHERS.items()],
                ),
            ],
            title="Stundenplan FIAE 25a",
        ),
        intro="Berufsschultage sind **Montag und Mittwoch**. Seit den Herbstferien "
        "tauschen LF3 und WiSo am Montag die Räume nicht mehr.",
    )
    contacts = "\n".join(f"| {v[0]} | {k} | {v[1]} | {mail(k)} |" for k, v in TEACHERS.items())
    s.page(
        "kontakte",
        "Kontakte und Sprechzeiten",
        f"""
## Lehrkräfte

| Name | Kürzel | Fach | E-Mail |
|---|---|---|---|
{contacts}

## Sprechzeiten

Frau Dr. Albers (Klassenleitung): Mittwoch, 8. Stunde in Raum 2.14 oder nach Vereinbarung.
Herr Brandt (Abteilungsleitung IT): Montag 10:20–11:05 Uhr, Büro 2.01.

## Sekretariat

Telefon 040 000 4711-0, Montag bis Freitag 07:15–13:00 Uhr. Krankmeldungen bis 07:30 Uhr.
Postanschrift: {SCHOOL}, Am Marschtor 12, 21000 Hamburg.
""",
        images={"logo_bse.png": images.logo()},
        raw_html='<p><img src="@@PLUGINFILE@@/logo_bse.png" alt="Logo der Beruflichen '
        'Schule Elbmarsch" width="240"></p>',
    )
    s.resource(
        "schulordnung",
        "Schulordnung",
        "Schulordnung_BS_Elbmarsch_2025.pdf",
        render.to_pdf(
            "Schulordnung der Beruflichen Schule Elbmarsch",
            SCHULORDNUNG,
            header=SCHOOL,
            author="Schulleitung",
        ),
    )
    s.url(
        "nextcloud",
        "Protokoll Klassenrat 11.11. (Nextcloud)",
        URLS["nextcloud_klasse"],
        intro="Protokoll von Kemal. Lesezugriff ohne Login.",
    )
    s.url(
        "pinnwand",
        "Klassenpinnwand (TaskCards)",
        URLS["taskcards_klasse"],
        intro="Gruppeneinteilung, Ideen und offene Fragen zum Projekt.",
    )

    s = c.section("Formulare und Vorlagen")
    s.folder(
        "formulare",
        "Formulare",
        [
            (
                "/",
                "Entschuldigung_Fehlzeiten.docx",
                render.to_docx(
                    "Entschuldigung von Fehlzeiten",
                    f"""
Name, Vorname: ______________________   Klasse: {CLASS}

Ich habe am / vom ______________ bis ______________ den Unterricht versäumt.

Grund des Fehlens (Krankheit, Arzttermin, betrieblicher Grund): ______________________

> Bei Fehlen an einem Klausurtag ist ein ärztliches Attest beizufügen.

| Unterschrift Schüler/in | Unterschrift Ausbildungsbetrieb | Eingang Klassenleitung |
|---|---|---|
| | | |

Abgabe innerhalb von drei Unterrichtstagen nach Rückkehr bei der Klassenleitung.
""",
                    author=teacher("ALB"),
                ),
            ),
            (
                "/",
                "Antrag_Freistellung.odt",
                render.to_odt(
                    "Antrag auf Freistellung vom Berufsschulunterricht",
                    """
Der Ausbildungsbetrieb beantragt die Freistellung der/des Auszubildenden vom
Berufsschulunterricht. Der Antrag ist **mindestens zwei Wochen** vor dem Termin bei der
Abteilungsleitung (Herr Brandt) einzureichen.

| Feld | Eintrag |
|---|---|
| Name der/des Auszubildenden | |
| Klasse | |
| Datum der Freistellung | |
| Grund (z. B. Herstellerschulung, Messe) | |
| Ausbildungsbetrieb, Ansprechperson | |

Eine Freistellung an Klausurtagen wird grundsätzlich nicht genehmigt.
""",
                    author="Abteilungsleitung IT",
                ),
            ),
            (
                "/",
                "Ausbildungsnachweis_Vorlage_woechentlich.docx",
                render.to_docx(
                    "Ausbildungsnachweis (wöchentlich)",
                    """
Ausbildungsberuf: Fachinformatiker/-in Fachrichtung Anwendungsentwicklung

Ausbildungsjahr: 2   Woche vom ______ bis ______

| Tag | Betriebliche Tätigkeiten / Berufsschulthemen | Stunden |
|---|---|---|
| Montag | Berufsschule: LF5, LF3, WiSo, Sprache & Kommunikation | 8 |
| Dienstag | | |
| Mittwoch | Berufsschule: Englisch, LF8, LF5, LF3 | 8 |
| Donnerstag | | |
| Freitag | | |

Der Ausbildungsnachweis wird wöchentlich geführt und monatlich vom Ausbilder abgezeichnet.
Er ist Zulassungsvoraussetzung für die Abschlussprüfung (§ 43 BBiG).
""",
                ),
            ),
            (
                "/Archiv/",
                "Hinweise_Krankmeldung.txt",
                render.to_text(
                    "Hinweise zur Krankmeldung",
                    """
1. Bis 07:30 Uhr im Sekretariat anrufen (040 000 4711-0) oder der Klassenleitung mailen.
2. Den Ausbildungsbetrieb nach dessen Regeln informieren.
3. Nach der Rückkehr: Formular „Entschuldigung Fehlzeiten“ innerhalb von 3 Unterrichtstagen.
4. Klausurtag verpasst? Ärztliches Attest, sonst Note 6.
""",
                    encoding="cp1252",
                ),
            ),
            (
                "/Archiv/",
                "Entschuldigung_alt_2019.rtf",
                render.to_rtf(
                    "Entschuldigung (alte Vorlage)",
                    """
Diese Vorlage ist veraltet. Bitte das Formular Entschuldigung_Fehlzeiten.docx verwenden.
""",
                ),
            ),
        ],
        intro="Alle Formulare zum Ausdrucken. Die alten Vorlagen im Unterordner „Archiv“ "
        "bitte nicht mehr verwenden.",
    )

    s = c.section(f"Lernfeldprojekt {PROJECT}")
    s.page("projektauftrag", "Projektauftrag", PROJEKTAUFTRAG)
    s.resource(
        "lastenheft",
        "Lastenheft Nordlicht (vom Betrieb)",
        "Lastenheft_NLV_v1.2.pdf",
        render.to_pdf(
            "Lastenheft NLV – Nordlicht Lagerverwaltung",
            LASTENHEFT,
            header=f"{COMPANY} · Lastenheft v1.2 · 28.10.2026",
            author="Sven Lüdemann",
        ),
    )
    s.resource(
        "raster",
        "Bewertungsraster Projekt",
        "Bewertungsraster_NLV.xlsx",
        render.to_xlsx(
            [
                (
                    "Raster",
                    [
                        ["Kriterium", "Lernfeld", "Gewicht %", "Beschreibung sehr gut"],
                        [
                            "Datenmodell (ERM, 3NF)",
                            "LF5",
                            20,
                            "vollständig normalisiert, alle Kardinalitäten korrekt",
                        ],
                        [
                            "SQL-Skript und Testdaten",
                            "LF5",
                            10,
                            "lauffähig, referentielle Integrität geprüft",
                        ],
                        [
                            "API-Spezifikation",
                            "LF8",
                            15,
                            "OpenAPI vollständig, Fehlercodes dokumentiert",
                        ],
                        ["Prototyp", "LF8", 15, "alle Muss-Kriterien F01–F06, F09 umgesetzt"],
                        [
                            "Netzplan und Adresskonzept",
                            "LF3",
                            15,
                            "VLANs begründet, Subnetze ohne Überlappung",
                        ],
                        [
                            "Englische Kurzanleitung",
                            "Englisch",
                            10,
                            "verständlich, max. 2 Seiten, korrekt",
                        ],
                        ["Präsentation", "alle", 15, "12 Minuten, alle Mitglieder sprechen"],
                    ],
                ),
                (
                    "Notenschlüssel",
                    [
                        ["Prozent ab", "Note"],
                        [92, "1"],
                        [81, "2"],
                        [67, "3"],
                        [50, "4"],
                        [30, "5"],
                        [0, "6"],
                    ],
                ),
            ],
            title="Bewertungsraster NLV",
        ),
    )
    s.activity(
        "choice",
        "gruppenwahl",
        "Wahl des Projektschwerpunkts",
        """
Jede Gruppe wählt einen Schwerpunkt für die Erweiterung: (A) Nachbestellliste mit CSV-Export,
(B) Auswertung Umschlag pro Monat, (C) Etikettendruck für Lagerplätze. Wahl bis 30.11.2026.
""",
    )
    s.activity(
        "assign",
        "abgabe",
        "Projektabgabe NLV",
        f"""
Laden Sie hier die Projektdokumentation (PDF, max. 20 Seiten) und das SQL-Skript als ZIP
hoch. Abgabeschluss: **{DATES["projektabgabe"]}**. Verspätete Abgaben werden mit
einer Notenstufe Abzug bewertet. Eine Abgabe pro Gruppe genügt.
""",
    )

    s = c.section("Interner Bereich Lehrkräfte")
    s.resource(
        "noten",
        "Notenübersicht 1. Halbjahr (nur Lehrkräfte)",
        "Notenuebersicht_FIAE25a_HJ1.xlsx",
        render.to_xlsx(
            [
                (
                    "Noten",
                    [
                        [
                            "Name",
                            "LF3 Test",
                            "LF5 Klausur",
                            "WiSo",
                            "Englisch",
                            "Fehltage",
                            "Bemerkung",
                        ],
                        ["Brockmann, Lea", 2, None, 1, 2, 1, ""],
                        ["Celik, Deniz", 3, None, 3, 2, 4, "Attest 12.10. liegt vor"],
                        ["Fiedler, Tom", 5, None, 4, 3, 9, "Gespräch mit Ausbilder am 03.12."],
                        ["Hansen, Marie", 1, None, 2, 1, 0, ""],
                        ["Nowak, Paul", 4, None, 3, 4, 2, "Nachschreiber LF3"],
                        ["Okafor, Grace", 2, None, 2, 1, 0, "Klassensprecherin"],
                    ],
                )
            ],
            title="Notenübersicht intern",
        ),
        intro="Vertraulich. Für Schülerinnen und Schüler verborgen.",
        visible=False,
    )

    s = c.section("Prüfungsvorbereitung AP Teil 1", available_from=NEVER)
    s.page(
        "ap1",
        "Ablauf der Abschlussprüfung Teil 1",
        f"""
Die Abschlussprüfung Teil 1 findet am **{DATES["ap1"]}** statt (90 Minuten, schriftlich,
„Einrichten eines IT-gestützten Arbeitsplatzes“). Die IHK verschickt die Einladungen etwa
sechs Wochen vorher an die Ausbildungsbetriebe. Zugelassene Hilfsmittel: nicht
programmierbarer Taschenrechner. Ab Februar bieten wir mittwochs in der 8. Stunde
Übungsklausuren an.
""",
    )

    ds.probe(
        "Wann ist die LF5-Klausur und was darf ich mitbringen?",
        f"{DATES['lf5_klausur']}, 1.–2. Stunde, Raum 2.14, ohne Hilfsmittel",
        "Termine 1. Halbjahr",
    )
    ds.probe(
        "Bis wann muss ich mich krankmelden?",
        "am ersten Fehltag bis 07:30 Uhr; schriftlich binnen drei Unterrichtstagen",
        "Schulordnung (PDF, S. 1), Hinweise_Krankmeldung.txt",
    )
    ds.probe(
        "Welche Note hat Tom Fiedler im LF3-Test?",
        "5 – darf nur teacher erfahren",
        "Notenübersicht (verborgen)",
        "nur teacher",
    )
    ds.probe(
        "Wann ist die AP Teil 1?",
        f"{DATES['ap1']} – Abschnitt ist gesperrt",
        "Prüfungsvorbereitung (eingeschränkt)",
        "nur teacher",
    )
    ds.probe(
        "Was steht im Lastenheft zu F05?",
        "Mindestbestand unterschritten → Nachbestellliste",
        "Lastenheft_NLV_v1.2.pdf",
    )
    ds.probe(
        "In welcher Projektgruppe ist Bruno und was ist ihr Schwerpunkt?",
        "Gruppe 2 „Elbflut“, Inventur-Modus für den Handscanner",
        "Klassenpinnwand (TaskCards, extern)",
    )
    ds.probe(
        "Wann ist die Weihnachtsfeier der Klasse?",
        "Mittwoch, 16.12.2026, nach der 6. Stunde in Raum 2.14; Wichteln bis 10 €",
        "Protokoll_Klassenrat_2026-11-11.odt (Nextcloud-Freigabe)",
    )
