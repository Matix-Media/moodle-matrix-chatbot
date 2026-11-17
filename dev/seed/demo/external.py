"""Content published on real external services, and where it lives.

The demo dataset links to TaskCards boards, HackMD notes, Google Docs/Slides/Sheets and
Nextcloud shares. Those documents were created by hand (the services need an account)
from the text below, so this file is their source of truth: if a document is lost,
recreate it from here and update its URL. ``dev/seed/check_demo_links.py`` fetches every
link through the API's own adapters and reports what no longer works.

Everything here is as fictional as the rest of the dataset.
"""

from __future__ import annotations

from .school import COMPANY, DATES, PROJECT

# --------------------------------------------------------------------------- #
# Where each document lives
# --------------------------------------------------------------------------- #

URLS = {
    "taskcards_klasse": "https://www.taskcards.de/#/board/9faa5698-c355-4fcf-bef3-1e03c46a81a5/view",
    "taskcards_lf8": "https://www.taskcards.de/#/board/c83a5f65-6906-4a05-8684-d4902cd87339/view",
    "hackmd_lf5": "https://hackmd.io/ZR2qMxAQQLWer0NDvgGfkg",
    "hackmd_phrasebank": "https://hackmd.io/fnU6lMEIRmqtsTN3JJ2XIw",
    "gdoc_glossar": "https://docs.google.com/document/d/1pBucnLzn0EYG3buGSUFz9_4pXGRllmDOn1383lZFF_0/edit?usp=sharing",
    "gslides_wiso": "https://docs.google.com/presentation/d/1qTX4_NcsPC1Wm7ZppXKGU-r1veZX76INhCXfT_yRGMI/edit?usp=sharing",
    "gsheet_verguetung": "https://docs.google.com/spreadsheets/d/1-HBKoVB-M-DwtrcLJUGTKk6W5jmuVRqUAThjiPoYYvs/edit?usp=sharing",
    "gdoc_roleplay": "https://docs.google.com/document/d/1TOJt8Q261ciXzP19TiPDIVgzhsirVUbFz2oyq35QBJs/edit?usp=sharing",
    "nextcloud_klasse": "https://fie.nl.tab.digital/s/5DKWa6z6tDKdymo",
    "nextcloud_lf8": "https://fie.nl.tab.digital/s/XKy75KCWPKtD74N",
    # Real videos with transcripts, not ours.
    "youtube_lf5": "https://www.youtube.com/watch?v=GFQaEYEc8_8",
    "youtube_lf3": "https://www.youtube.com/watch?v=ipojl8kZT10",
    "youtube_en": "https://www.youtube.com/watch?v=Jpaz3YuCil4",
}

# --------------------------------------------------------------------------- #
# TaskCards
# --------------------------------------------------------------------------- #

#: Board name, then (list name, [(card title, card description)]).
TASKCARDS_KLASSE = (
    "FIAE 25a – Klassenpinnwand Projekt NLV",
    [
        (
            "Gruppen",
            [
                (
                    "Gruppe 1 „Polarstern“",
                    "Lea Brockmann, Tom Fiedler, Sophie Brandes, Kemal Arslan. "
                    "Schwerpunkt Erweiterung: Nachbestellliste mit CSV-Export.",
                ),
                (
                    "Gruppe 2 „Elbflut“",
                    "Bruno Kowalski, Grace Okafor, Finn Lorenz, Jana Wolff. "
                    "Schwerpunkt Erweiterung: Inventur-Modus für den Handscanner.",
                ),
                (
                    "Gruppe 3 „Kranhaus“",
                    "Noah Becker, Emily Nguyen, Deniz Celik, Marie Hansen. "
                    "Schwerpunkt Erweiterung: Lagerplatz-Suche nach freier Kapazität.",
                ),
                (
                    "Gruppe 4 „Containerbrücke“",
                    "Paul Nowak, Leonie Krause, Jonas Meier, Aylin Demirci. "
                    "Schwerpunkt Erweiterung: Dashboard mit Tagesumschlag je Halle.",
                ),
            ],
        ),
        (
            "Offene Fragen an Nordlicht",
            [
                (
                    "Wie viele Handscanner gibt es in Halle 4?",
                    "Für das Adresskonzept in LF3 wichtig. Herr Lüdemann klärt das bis zur "
                    f"Betriebsbesichtigung am {DATES['besichtigung']}.",
                ),
                (
                    "Dürfen Artikelnummern wiederverwendet werden?",
                    "Antwort von Herrn Lüdemann (12.11.): Nein. Eine einmal vergebene "
                    "Artikelnummer bleibt für immer gesperrt, auch wenn der Artikel ausläuft.",
                ),
                (
                    "Gibt es Chargen oder Mindesthaltbarkeitsdaten?",
                    "Nur bei Getränken (Kunde Elbe Getränke). Für den Prototyp ignorieren, "
                    "aber im Ausblick der Doku erwähnen.",
                ),
            ],
        ),
        (
            "Ideen",
            [
                (
                    "Farbcode für Lagerplätze",
                    "Rot = gesperrt, Gelb = unter Mindestbestand, Grün = ok. Idee von Gruppe 3.",
                ),
                (
                    "Gemeinsame Testdaten",
                    "Alle Gruppen nutzen dieselben Testdaten aus nlv_schema.sql aus LF5, damit "
                    "wir die APIs gegenseitig testen können.",
                ),
            ],
        ),
        (
            "Organisatorisches",
            [
                (
                    "Klassensprecherin",
                    "Grace Okafor, Vertretung Kemal Arslan (gewählt am 07.09.2026).",
                ),
                (
                    "Abgabe Projektdoku",
                    f"{DATES['projektabgabe']} als PDF im Moodle-Kurs Klassenraum. Eine Abgabe "
                    "pro Gruppe.",
                ),
            ],
        ),
    ],
)

TASKCARDS_LF8 = (
    "LF8 – Projektboard Scanner-API (NLV)",
    [
        (
            "Backlog",
            [
                (
                    "GET /lagerplaetze?frei=true",
                    "Liefert freie Lagerplätze einer Halle. Gruppe 3 (Kranhaus).",
                ),
                (
                    "Authentifizierung per API-Key",
                    "Header X-Api-Key, ein Key pro Handscanner. Noch nicht verteilt.",
                ),
            ],
        ),
        (
            "In Arbeit",
            [
                (
                    "POST /buchungen",
                    "Ein- und Auslagerung buchen. Antwort 201 mit Location-Header, 409 "
                    "Conflict wenn der Bestand negativ würde. Verantwortlich: Bruno Kowalski "
                    "(Gruppe 2).",
                ),
                (
                    "GET /artikel/{artikel_nr}",
                    "Artikel mit Bestand je Lager. 404 bei unbekannter Nummer. Verantwortlich: "
                    "Jana Wolff (Gruppe 2).",
                ),
            ],
        ),
        (
            "Review",
            [
                (
                    "OpenAPI-Spezifikation v0.3",
                    "Liegt als openapi.yaml im Gruppenordner. Review durch Herrn Brandt bis "
                    "16.12.2026 (Meilenstein M3).",
                ),
            ],
        ),
        (
            "Fertig",
            [
                (
                    "Datenformat für Buchungen festgelegt",
                    "JSON, Felder artikel_nr, lager_id, menge (negativ = Auslagerung), "
                    "scanner_id. Zeitstempel setzt der Server.",
                ),
                (
                    "Testumgebung",
                    "Postman-Collection und Mock-Server laufen. Port 8080 im Labor 0.11.",
                ),
            ],
        ),
    ],
)

# --------------------------------------------------------------------------- #
# HackMD
# --------------------------------------------------------------------------- #

HACKMD_LF5 = f"""# LF5 Datenbanken – Gemeinsame Mitschrift FIAE 25a

Jede Woche schreiben zwei Personen mit (Reihenfolge laut Klassenliste). Bitte unter das
richtige Datum schreiben und nichts löschen, was andere geschrieben haben.

## Montag, 02.11.2026 – Protokoll: Lea Brockmann, Kemal Arslan

- Wiederholung ERM: Entität, Attribut, Beziehung, Kardinalitäten 1:1, 1:n, n:m.
- Frau Dr. Albers: Eine n:m-Beziehung wird im Relationenmodell **immer** über eine
  Zwischentabelle aufgelöst. Beispiel aus NLV: `auftragsposition` zwischen `auftrag` und
  `artikel`.
- Hausaufgabe: ERM für das NLV-Projekt skizzieren (Lager, Lagerplatz, Artikel, Bestand).

## Mittwoch, 04.11.2026 – Protokoll: Lea Brockmann, Kemal Arslan

- Besprechung der Hausaufgabe. Häufigster Fehler: Bestand als Attribut von Artikel statt
  als eigene Beziehung zwischen Artikel und Lager.
- Merksatz: „Bestand gehört dem Lagerplatz, nicht dem Artikel.“

## Montag, 09.11.2026 – Protokoll: Grace Okafor, Noah Becker

- Normalisierung: 1. NF (atomare Werte), 2. NF (volle funktionale Abhängigkeit vom
  gesamten Schlüssel), 3. NF (keine transitiven Abhängigkeiten).
- Beispiel: In der alten Excel-Mappe von Nordlicht steht der Kundenort in jeder
  Auftragszeile → Verstoß gegen die 3. NF.
- Frage von Noah: Ist eine Postleitzahl → Ort eine transitive Abhängigkeit? Antwort: Ja,
  in der Praxis wird sie aber oft bewusst in Kauf genommen (Denormalisierung).

## Mittwoch, 11.11.2026 – Protokoll: Grace Okafor, Noah Becker

- SQL: `INNER JOIN` vs. `LEFT JOIN`. Bei `LEFT JOIN` bleiben alle Zeilen der linken
  Tabelle erhalten, fehlende Werte rechts werden `NULL`.
- Übung im Arbeitsblatt LF5_AB_JOINs, Aufgaben 1–4 gemeinsam besprochen.
- **Hausaufgabe bis Mittwoch, 18.11.2026:** Aufgaben 5 und 6 des Arbeitsblatts JOINs und
  Meilenstein M1 (ERM und Relationenschema) vorbereiten.

## Montag, 16.11.2026 – Protokoll: Finn Lorenz, Marie Hansen

- Aggregatfunktionen: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` und `GROUP BY`.
- `HAVING` filtert Gruppen nach dem Gruppieren, `WHERE` filtert Zeilen davor.
- Frau Dr. Albers hat angekündigt: In der Klausur am {DATES["lf5_klausur"]} kommt
  **sicher** eine Aufgabe mit `GROUP BY` und `HAVING` dran. Die Probeklausur liegt im
  Moodle-Kurs.
- Die Abnahme von M1 ist am Montag, 30.11.2026, in der 1.–2. Stunde (Raum 2.14). Jede
  Gruppe hat zehn Minuten.

## Offene Fragen

- Dürfen wir in der Klausur `JOIN ... USING` statt `ON` schreiben? → Frau Dr. Albers: ja,
  beides ist richtig.
"""

HACKMD_PHRASEBANK = """# Phrasebank: Customer support on the phone and by e-mail

FIAE 25a · English · Mr Whitfield. Add your own phrases at the bottom, with an example.

## Opening a call

- "Good morning, IT help desk, this is … speaking. How can I help you?"
- "Could I have your name and the number of your scanner, please?"

## Asking questions (the **A** in LAST)

- "Since when has this been happening?"
- "What exactly does the error message say?"
- "Have you already tried restarting the device?"

## Apologising and calming the caller

- "I'm sorry for the inconvenience."
- "I do apologise for the delay – we are working on it."
- "I completely understand that this is frustrating."

## Offering a solution or workaround

- "As a workaround, you could book the items on the PC in the office."
- "I'll open a ticket and a colleague will call you back within two hours."

## Closing (the **T** in LAST)

- "Is there anything else I can help you with?"
- "Thank you for calling, have a nice day."

## E-mail phrases

- "Thank you for your e-mail regarding …"
- "Please find attached the updated user guide."
- "Please do not hesitate to contact us if you have any further questions."

## Added by the class

- "Could you spell that, please?" – for article numbers like NL-03141 (Sophie)
- "Let me just check that for you." – while you look something up (Bruno)
"""

# --------------------------------------------------------------------------- #
# Google Docs / Slides / Sheets
# --------------------------------------------------------------------------- #

GDOC_GLOSSAR = """WiSo-Glossar FIAE 25a

Jede Person ergänzt mindestens drei Fachbegriffe mit Erklärung. Alphabetisch einsortieren!

Abmahnung – Schriftliche Rüge des Arbeitgebers wegen eines konkreten Fehlverhaltens, \
verbunden mit der Androhung einer Kündigung im Wiederholungsfall.

Ausbildungsnachweis (Berichtsheft) – Wird wöchentlich geführt und ist Voraussetzung für die \
Zulassung zur Abschlussprüfung (§ 43 BBiG). Bei Nordlicht digital in der App „Azubi-Log“.

Berufsbildungsgesetz (BBiG) – Regelt die duale Berufsausbildung: Vertrag, Probezeit, Pflichten \
von Auszubildenden und Ausbildenden, Kündigung, Prüfungen.

Betriebsrat – Gewählte Vertretung der Belegschaft nach dem Betriebsverfassungsgesetz. Kann ab \
fünf wahlberechtigten Beschäftigten gewählt werden.

Flächentarifvertrag – Gilt für alle tarifgebundenen Betriebe einer Branche in einer Region, \
z. B. Logistik Hamburg.

Friedenspflicht – Während der Laufzeit eines Tarifvertrags darf nicht für Ziele gestreikt \
werden, die in diesem Vertrag geregelt sind.

Haustarifvertrag – Tarifvertrag zwischen einer Gewerkschaft und einem einzelnen Unternehmen.

Jugend- und Auszubildendenvertretung (JAV) – Vertritt Beschäftigte unter 18 und Auszubildende \
unter 25 Jahren. Bei Nordlicht wird sie am 26.11.2026 gewählt.

Manteltarifvertrag – Regelt die allgemeinen Arbeitsbedingungen wie Arbeitszeit, Urlaub und \
Kündigungsfristen für eine längere Laufzeit. Die Höhe der Vergütung steht dagegen im \
Entgelt- bzw. Vergütungstarifvertrag.

Probezeit – In der Ausbildung mindestens einen und höchstens vier Monate (§ 20 BBiG). In der \
Probezeit kann ohne Frist und ohne Grund gekündigt werden, aber nur schriftlich.

Sozialversicherung – Fünf Säulen: Kranken-, Pflege-, Renten-, Arbeitslosen- und \
Unfallversicherung. Die Unfallversicherung zahlt allein der Arbeitgeber.

Urlaubsanspruch – Nach dem Jugendarbeitsschutzgesetz für 17-Jährige mindestens 27 Werktage; \
laut Tarifvertrag Logistik Hamburg 30 Arbeitstage.

Vermögenswirksame Leistungen (VL) – Zuschuss des Arbeitgebers für langfristiges Sparen, laut \
Tarifvertrag 26,59 € im Monat.
"""

#: Slides: (title, body lines).
GSLIDES_WISO = [
    (
        "Ausbildungsrecht – Ergebnisse der Gruppenarbeit",
        ["WiSo · FIAE 25a · 09.11.2026", "Frau Demir"],
    ),
    (
        "Gruppe 1: Pflichten der Auszubildenden (§ 13 BBiG)",
        [
            "Lernpflicht: sich bemühen, die Kenntnisse zu erwerben",
            "Berufsschulpflicht und Teilnahme an Prüfungen",
            "Weisungen befolgen, Betriebsordnung einhalten",
            "Schweigepflicht über Betriebs- und Geschäftsgeheimnisse",
            "Ausbildungsnachweis führen",
        ],
    ),
    (
        "Gruppe 2: Pflichten der Ausbildenden (§ 14 BBiG)",
        [
            "Ausbildungsmittel kostenlos stellen (z. B. Laptop, Fachbücher)",
            "Für den Berufsschulbesuch freistellen",
            "Nur Tätigkeiten übertragen, die dem Ausbildungszweck dienen",
            "Beispiel aus Fall 2: Firmenwagen waschen ist ausbildungsfremd",
        ],
    ),
    (
        "Gruppe 3: Urlaub und Arbeitszeit",
        [
            "JArbSchG: unter 18 höchstens 8 Stunden täglich und 40 Stunden pro Woche",
            "Berufsschultag mit mehr als 5 Unterrichtsstunden à 45 Minuten zählt als 8 Stunden "
            "– einmal pro Woche, danach keine Rückkehr in den Betrieb",
            "Urlaub laut Tarifvertrag: 30 Arbeitstage",
        ],
    ),
    (
        "Gruppe 4: Kündigung",
        [
            "In der Probezeit: jederzeit ohne Frist, schriftlich",
            "Nach der Probezeit durch den Betrieb nur aus wichtigem Grund",
            "Durch Auszubildende mit vier Wochen Frist bei Berufsaufgabe",
            "Fall 1 (Lina): Kündigung am 20.11. ist wirksam – die Probezeit läuft bis 30.11.",
        ],
    ),
]

#: Sheets: rows of the first tab. Formulas are in the sheet's German locale (``;`` separates
#: arguments, ``,`` is the decimal mark).
GSHEET_VERGUETUNG = [
    ["Rechner Ausbildungsvergütung (Lehrbeispiel, Tarif Logistik Hamburg 2026)", "", ""],
    ["Ausbildungsjahr (1–3)", 2, "← hier eintragen"],
    ["Vergütung brutto", "=CHOOSE(B2;1085;1160;1245)", "laut Tarifvertrag"],
    ["Vermögenswirksame Leistungen", 26.59, ""],
    ["Gesamtbrutto", "=B3+B4", ""],
    ["Krankenversicherung AN (8,75 %)", "=ROUND(B5*0,0875;2)", "7,3 % + halber Zusatzbeitrag"],
    ["Pflegeversicherung AN (1,8 %)", "=ROUND(B5*0,018;2)", "kinderlos unter 23: kein Zuschlag"],
    ["Rentenversicherung AN (9,3 %)", "=ROUND(B5*0,093;2)", ""],
    ["Arbeitslosenversicherung AN (1,3 %)", "=ROUND(B5*0,013;2)", ""],
    ["Summe Sozialversicherung", "=SUM(B6:B9)", ""],
    ["Lohnsteuer (vereinfacht)", "=IF(B5>1300;ROUND((B5-1300)*0,14;2);0)", "nur grobe Schätzung"],
    ["Netto", "=B5-B10-B11", ""],
    ["Jahressonderzahlung (November)", "=B3*0,5", "50 % der Monatsvergütung"],
]

GDOC_ROLEPLAY = """Role play notes – Unit 3 Customer support (FIAE 25a)

Write down what went well and what you would say differently next time.

Pair 1 – Sophie (caller) and Leonie (help desk)
Problem: Scanner in hall 2 shows "Item not found" for all articles starting with NL-0.
Went well: Leonie followed the LAST pattern and asked for the scanner number first.
Better next time: Don't say "I don't know" – say "Let me just check that for you."
Solution found: The article list on the scanner was out of date. Workaround: sync the scanner \
in the docking station, ticket #4471 opened.

Pair 2 – Bruno (caller) and Emily (help desk)
Problem: The label printer in hall 1 prints empty labels.
Went well: Polite opening and closing, good phrases for apologising.
Better next time: Ask "Since when has this been happening?" earlier – it took three minutes \
to find out the problem started after the paper roll was changed.
Solution found: Thermal paper was inserted the wrong way round.

Pair 3 – Noah (caller) and Aylin (help desk)
Problem: Caller cannot log in to the warehouse app after a password change.
Went well: Aylin spelled the username back using the NATO alphabet.
Better next time: Never ask the caller to tell you their password on the phone!
Solution found: Caps Lock was on.

Mr Whitfield's feedback for everyone
Use "Could you…" instead of "Can you…" – it sounds more polite.
The caller should always know the next step and when they will hear from you.
In the vocabulary test on 25 November there will be one question about this role play.
"""

# --------------------------------------------------------------------------- #
# Nextcloud (single files, so the share's /download returns that file)
# --------------------------------------------------------------------------- #

NEXTCLOUD_KLASSE_FILENAME = "Protokoll_Klassenrat_2026-11-11.odt"
NEXTCLOUD_KLASSE = f"""
# Protokoll der Klassenratssitzung FIAE 25a

Mittwoch, 11.11.2026, 8. Stunde (14:10–14:55 Uhr), Raum 2.14. Leitung: Grace Okafor
(Klassensprecherin). Protokoll: Kemal Arslan. Anwesend: 22 von 24 Schülerinnen und Schülern,
Frau Dr. Albers (Klassenlehrerin).

## TOP 1: Weihnachtsfeier

Die Klasse feiert am Mittwoch, 16.12.2026, nach der 6. Stunde im Raum 2.14. Jede Person
bringt etwas zu essen mit. Wichteln mit einem Limit von 10 €. Organisation: Jana Wolff und
Deniz Celik.

## TOP 2: Lautstärke im Labor 0.11

Mehrere Personen beschweren sich über die Lautstärke während der Gruppenarbeit in LF3.
Beschluss: Herr Petersen wird gebeten, für Gruppenarbeit zusätzlich Raum 0.12 zu öffnen.

## TOP 3: Projekt NLV

Die Gruppen fühlen sich mit dem Termin für Meilenstein M3 (16.12.2026) unter Druck, weil
in derselben Woche die WiSo-Klausur ({DATES["wiso_klausur"]}) geschrieben wird. Frau
Dr. Albers spricht mit Herrn Brandt, ob M3 auf den 18.12.2026 verschoben werden kann.

## TOP 4: Verschiedenes

- Die Betriebsbesichtigung bei {COMPANY} am {DATES["besichtigung"]} beginnt um 08:30 Uhr am
  Tor 3 in Billbrook. Sicherheitsschuhe werden gestellt.
- Nächster Klassenrat: Mittwoch, 13.01.2027.
"""

NEXTCLOUD_LF8_FILENAME = "Vorlage_Projektdokumentation_NLV.odt"
NEXTCLOUD_LF8 = f"""
# Vorlage: Projektdokumentation {PROJECT}

Diese Vorlage gibt die Gliederung vor. Umfang: höchstens 15 Seiten ohne Anhang, Schrift
Arial 11 pt, Zeilenabstand 1,5. Abgabe als PDF bis {DATES["projektabgabe"]}.

## 1. Einleitung

Ausgangssituation bei {COMPANY}, Projektziel, Abgrenzung (was gehört nicht zum Prototyp).

## 2. Projektplanung

Gruppenmitglieder und Zuständigkeiten, Zeitplan mit den Meilensteinen M1–M6, eingesetzte
Werkzeuge (z. B. TaskCards-Board, Git, Postman).

## 3. Datenmodell (LF5)

ERM, Relationenschema in 3. Normalform, Begründung für jede n:m-Auflösung.

## 4. Schnittstelle (LF8)

Endpunkte mit Methode, Pfad, Statuscodes und Beispiel-JSON. Die OpenAPI-Datei gehört in
den Anhang.

## 5. Netzwerkkonzept Halle 4 (LF3)

Netzplan, VLANs und Adresstabelle.

## 6. Test

Mindestens fünf Testfälle in einer Tabelle: Eingabe, erwartetes Ergebnis, tatsächliches
Ergebnis. Ein Testfall muss den Statuscode 409 Conflict prüfen.

## 7. Fazit und Ausblick

## Anhang

Quellcode-Auszüge, OpenAPI-Datei, englische Kurzanleitung (aus dem Englischunterricht).

**Bewertung:** Die Dokumentation zählt 40 %, der Prototyp 40 % und die Präsentation 20 %
der Projektnote.
"""
