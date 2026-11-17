"""LF8 Schnittstellen: HTTP, REST, JSON/XML – the scanner API of the Nordlicht project."""

from __future__ import annotations

import json

from .. import render
from ..external import URLS
from ..model import Dataset
from ..school import CLASS, COMPANY, DATES, HEADER, teacher

AUTHOR = teacher("BRA")

HTTP_FOLIEN = """
## Was ist HTTP?
- Zustandsloses Anfrage-Antwort-Protokoll auf Schicht 7
- Client schickt Request, Server antwortet mit Response
- HTTPS = HTTP über TLS (Port 443)

## Aufbau eines Requests
```
POST /api/v1/buchungen HTTP/1.1
Host: nlv.nordlicht.example
Content-Type: application/json
X-Api-Key: scn-h2-017-••••

{"artikel_nr": "NL-00815", "lager_id": 1, "menge": -6, "auftrag_id": 5002}
```

## Methoden
| Methode | Zweck | idempotent |
|---|---|---|
| GET | Ressource lesen | ja |
| POST | neue Ressource anlegen | nein |
| PUT | Ressource ersetzen | ja |
| PATCH | Ressource teilweise ändern | nein |
| DELETE | Ressource löschen | ja |
Notiz: Idempotenz an der Lagerbuchung erklären: zweimal dieselbe POST-Buchung = doppelte Auslagerung!

## Statuscodes
| Code | Bedeutung | Bei NLV |
|---|---|---|
| 200 OK | Erfolg | Bestand abgefragt |
| 201 Created | angelegt | Buchung gespeichert |
| 400 Bad Request | Anfrage fehlerhaft | Menge fehlt |
| 401 Unauthorized | nicht authentifiziert | API-Schlüssel ungültig |
| 404 Not Found | gibt es nicht | Artikel unbekannt |
| 409 Conflict | Konflikt | Bestand würde negativ (F04) |
| 500 Internal Server Error | Serverfehler | Datenbank nicht erreichbar |
"""

REST_SKRIPT = f"""
# 1. REST in fünf Regeln

REST (Representational State Transfer) ist ein Architekturstil, kein Protokoll. Für unsere
Schnittstelle bedeutet das:

1. **Ressourcen** werden über URLs angesprochen: /artikel/NL-04711, /lager/1/bestand.
2. **HTTP-Methoden** drücken die Aktion aus, nicht die URL (/artikel, nicht /getArtikel).
3. **Zustandslos**: jede Anfrage enthält alles Nötige, auch die Authentifizierung.
4. **Repräsentationen** sind bei uns JSON, auf Wunsch XML für das Altsystem der Spedition.
5. **Statuscodes** sagen, was passiert ist; der Body erklärt Details.

# 2. Endpunkte der NLV-Schnittstelle

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | /api/v1/artikel/{{artikel_nr}} | Artikel mit Beständen aller Lager |
| GET | /api/v1/lager/{{lager_id}}/bestand | Bestandsliste eines Lagers |
| POST | /api/v1/buchungen | Ein- oder Auslagerung buchen |
| GET | /api/v1/nachbestellliste | Artikel unter Mindestbestand (F05) |
| GET | /api/v1/nachbestellliste.csv | dasselbe als CSV (F08) |

# 3. Fehlerformat

Alle Fehler haben dasselbe JSON-Format, damit die Scanner-App sie einheitlich anzeigen kann:

```
{{"fehler": "BESTAND_NEGATIV", "meldung": "Bestand von NL-00815 in Lager 1 reicht nicht (140 < 200)", "status": 409}}
```

---

# 4. Authentifizierung

Jeder Handscanner hat einen eigenen API-Schlüssel im Header **X-Api-Key**. Schlüssel werden
in der Datenbank nur als Hash gespeichert. Verliert ein Gerät seinen Schlüssel, wird nur
dieser gesperrt.

# 5. Präsentation

Die Präsentationen finden am **{DATES["lf8_praesentation"]}** statt, je Gruppe 12 Minuten
plus 5 Minuten Fragen. Gezeigt werden müssen: eine erfolgreiche Buchung, eine Buchung mit
Fehler 409 und die Nachbestellliste.
"""

OPENAPI_MD = """
## Buchung anlegen

`POST /api/v1/buchungen`

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| artikel_nr | string | ja | Format NL-##### |
| lager_id | integer | ja | 1–4 |
| menge | integer | ja | positiv = Einlagerung, negativ = Auslagerung |
| auftrag_id | integer | bei Auslagerung | Auftrag, zu dem ausgelagert wird |

Antworten: 201 mit der Buchung, 400 bei fehlenden Feldern, 404 bei unbekanntem Artikel,
409 wenn der Bestand negativ würde.

## Nachbestellliste

`GET /api/v1/nachbestellliste` liefert alle Artikel mit bestand < mindestbestand, sortiert
nach Fehlmenge absteigend.
"""

BEISPIEL_JSON = {
    "artikel_nr": "NL-00815",
    "bezeichnung": "Stretchfolie 500 mm x 300 m",
    "einzelpreis": 11.90,
    "gesperrt": False,
    "bestaende": [
        {"lager_id": 1, "standort": "Halle 1 Billbrook", "bestand": 140, "mindestbestand": 200},
    ],
    "_links": {"self": "/api/v1/artikel/NL-00815", "lager": "/api/v1/lager/1/bestand"},
}

BEISPIEL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Avis an das Altsystem der Spedition Hanse-Trans (Format seit 2011) -->
<lieferavis version="2.1">
  <absender>Nordlicht Logistik GmbH</absender>
  <auftrag nr="5003" datum="2026-11-03">
    <kunde nr="3">Elbe Getränke GmbH</kunde>
    <position artikel="NL-02200" menge="1">Hubwagen 2,5 t</position>
    <position artikel="NL-03141" menge="12">Sicherheitsschuhe S3 Gr. 43</position>
  </auftrag>
</lieferavis>
"""


def build(ds: Dataset) -> None:
    c = ds.course(
        "lf8",
        "LF8 – Daten systemübergreifend bereitstellen",
        {"teacher": "editingteacher", "student2": "student"},
    )

    s = c.section("Allgemeines")
    s.label(
        "info",
        f"""
**LF8 bei {AUTHOR}**, mittwochs 3.–4. Stunde in Raum 2.16. In diesem Halbjahr entwickeln wir
die Scanner-Schnittstelle für das Projekt bei {COMPANY}.
""",
    )
    s.url(
        "taskcards",
        "Projektboard API-Gruppen (TaskCards)",
        URLS["taskcards_lf8"],
    )
    s.url(
        "nextcloud_vorlagen",
        "Vorlage Projektdokumentation (Nextcloud)",
        URLS["nextcloud_lf8"],
        intro="Gliederung und Formvorgaben für die Doku. Bitte nicht in der Vorlage arbeiten, "
        "sondern eine Kopie herunterladen.",
    )

    s = c.section("Thema 1: HTTP-Grundlagen")
    s.resource(
        "http",
        "Folien: HTTP",
        "LF8_HTTP_Grundlagen.pptx",
        render.to_pptx("HTTP-Grundlagen", HTTP_FOLIEN, subtitle=f"LF8 · {CLASS}"),
    )
    s.page(
        "werkzeuge",
        "Werkzeuge: curl und Bruno",
        """
Für Tests der Schnittstelle nutzen wir **curl** auf der Kommandozeile und den API-Client
**Bruno** (Open Source, speichert Anfragen als Dateien im Git-Repository).

```
curl -i -H "X-Api-Key: $KEY" https://nlv.nordlicht.example/api/v1/artikel/NL-04711
```

Die Option -i zeigt die Response-Header mit Statuscode an.
""",
    )

    s = c.section("Thema 2: REST und Datenformate")
    s.resource(
        "rest",
        "Skript: REST-Schnittstelle NLV",
        "LF8_Skript_REST.pdf",
        render.to_pdf(
            "Skript: Die REST-Schnittstelle der NLV",
            REST_SKRIPT,
            header=f"{HEADER} · LF8",
            author=AUTHOR,
        ),
    )
    s.folder(
        "formate",
        "Beispieldateien Datenformate",
        [
            (
                "/",
                "api_spezifikation.md",
                render.to_markdown("NLV-API – Spezifikation (Entwurf)", OPENAPI_MD),
            ),
            (
                "/",
                "artikel_NL-00815.json",
                json.dumps(BEISPIEL_JSON, ensure_ascii=False, indent=2).encode("utf-8"),
            ),
            ("/", "lieferavis_5003.xml", BEISPIEL_XML.encode("utf-8")),
            (
                "/",
                "nachbestellliste.csv",
                render.to_csv(
                    [
                        ["artikel_nr", "lager_id", "bestand", "mindestbestand", "fehlmenge"],
                        ["NL-00815", 1, 140, 200, 60],
                        ["NL-03141", 2, 25, 30, 5],
                    ]
                ),
            ),
            (
                "/Export Altsystem/",
                "index.html",
                render.to_html_document(
                    "Lagerliste Export",
                    """
| Artikel | Bezeichnung | Halle | Bestand |
|---|---|---|---|
| NL-04711 | Palettenregal 2,4 m | 1 | 36 |
| NL-00815 | Stretchfolie | 1 | 140 |
| NL-02200 | Hubwagen 2,5 t | 2 | 3 |

Export erzeugt am 02.11.2026 06:00 aus Lagerliste.xlsx.
""",
                ),
            ),
        ],
        intro="JSON für die Scanner, XML für das Altsystem der Spedition, CSV für den Export.",
    )
    s.activity(
        "quiz",
        "statuscodes",
        "Quiz: Statuscodes",
        "Ordnen Sie 12 Situationen den richtigen HTTP-Statuscodes zu. Zwei Versuche.",
    )
    s.activity(
        "assign",
        "openapi",
        "Abgabe Meilenstein M3: API-Spezifikation",
        """
Laden Sie die OpenAPI-Datei (YAML) Ihrer Gruppe bis **16.12.2026** hoch. Die Spezifikation
muss alle Endpunkte aus dem Skript, das Fehlerformat und die Authentifizierung enthalten.
""",
    )

    ds.probe(
        "Welchen Statuscode liefert die API, wenn der Bestand negativ würde?",
        "409 Conflict",
        "LF8_HTTP_Grundlagen.pptx, LF8_Skript_REST.pdf",
        "student2, teacher",
    )
    ds.probe(
        "Wann sind die LF8-Präsentationen?",
        DATES["lf8_praesentation"],
        "LF8_Skript_REST.pdf, Termine 1. Halbjahr",
        "alle (Skript nur student2/teacher)",
    )
    ds.probe(
        "Was steht im Lieferavis 5003?",
        "NL-02200 ×1, NL-03141 ×12 an Elbe Getränke "
        "(XML: Plugin lädt es hoch, die API hat keinen XML-Extraktor)",
        "lieferavis_5003.xml",
    )
    ds.probe(
        "Wer baut den Endpunkt POST /buchungen?",
        "Bruno Kowalski (Gruppe 2); Status: In Arbeit",
        "Projektboard API-Gruppen (TaskCards, extern)",
        "student2, teacher",
    )
    ds.probe(
        "Wie lang darf die Projektdokumentation sein?",
        "höchstens 15 Seiten ohne Anhang, Arial 11 pt, Zeilenabstand 1,5",
        "Vorlage_Projektdokumentation_NLV.odt (Nextcloud-Freigabe)",
        "student2, teacher",
    )
