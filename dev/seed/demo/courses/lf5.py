"""LF5 Datenbanken: ERM, normalisation, SQL, JOINs – built on the Nordlicht warehouse."""

from __future__ import annotations

from .. import images, render
from ..external import URLS
from ..model import NEVER, Dataset
from ..school import CLASS, COMPANY, DATES, HEADER, teacher

AUTHOR = teacher("ALB")

ERM_SKRIPT = f"""
# 1. Warum Datenmodellierung?

Bevor eine einzige Tabelle angelegt wird, beschreiben wir, **welche Dinge** (Entitäten)
eine Anwendung verwalten muss und **wie sie zusammenhängen** (Beziehungen). Das
Entity-Relationship-Modell (ERM) nach Peter Chen (1976) ist dafür das Standardwerkzeug.
Es ist unabhängig von einem bestimmten Datenbanksystem und dient als Gesprächsgrundlage
mit dem Auftraggeber – in unserem Projekt mit der {COMPANY}.

# 2. Grundbegriffe

| Begriff | Bedeutung | Beispiel Nordlicht |
|---|---|---|
| Entität | eindeutig unterscheidbares Objekt | der Artikel NL-04711 „Palettenregal 2,4 m“ |
| Entitätstyp | Menge gleichartiger Entitäten | Artikel |
| Attribut | Eigenschaft eines Entitätstyps | bezeichnung, gewicht_kg |
| Schlüsselattribut | identifiziert eine Entität eindeutig | artikel_nr |
| Beziehungstyp | Zusammenhang zwischen Entitätstypen | Kunde *erteilt* Auftrag |

# 3. Kardinalitäten

Die Kardinalität gibt an, mit wie vielen Entitäten der anderen Seite eine Entität in
Beziehung stehen kann. Wir verwenden in der Klasse die **Chen-Notation (1:1, 1:n, m:n)**
und lesen zur Kontrolle auch die Krähenfuß-Notation (Crow's Foot), weil sie in vielen
Werkzeugen wie MySQL Workbench verwendet wird.

- **1:1** – Ein Lager hat genau einen Lagerleiter, ein Mitarbeiter leitet höchstens ein Lager.
- **1:n** – Ein Kunde erteilt viele Aufträge, ein Auftrag gehört zu genau einem Kunden.
- **m:n** – Ein Auftrag enthält viele Artikel, ein Artikel kommt in vielen Aufträgen vor.

> Merksatz: Eine m:n-Beziehung wird im Relationenmodell **immer** in eine eigene Tabelle
> aufgelöst (Zwischentabelle). Bei Nordlicht ist das die Tabelle auftragsposition.

---

# 4. Vom ERM zum Relationenmodell

Die Überführung folgt drei Regeln:

1. Jeder Entitätstyp wird eine Tabelle; das Schlüsselattribut wird Primärschlüssel (PK).
2. Bei 1:n wandert der Primärschlüssel der 1-Seite als Fremdschlüssel (FK) in die n-Seite.
3. Bei m:n entsteht eine neue Tabelle, deren Primärschlüssel aus beiden Fremdschlüsseln
   zusammengesetzt ist; Beziehungsattribute (z. B. menge) gehören in diese Tabelle.

Für Nordlicht ergibt sich (Primärschlüssel unterstrichen, hier fett):

- kunde(**kunde_id**, firmenname, ansprechpartner, plz, ort, kunde_seit)
- auftrag(**auftrag_id**, kunde_id→kunde, lager_id→lager, auftragsdatum, lieferdatum, status)
- artikel(**artikel_nr**, bezeichnung, gewicht_kg, einzelpreis, kategorie_id→kategorie)
- auftragsposition(**auftrag_id→auftrag, artikel_nr→artikel**, menge, rabatt_prozent)
- lager(**lager_id**, standort, flaeche_qm, leiter_id→mitarbeiter)
- lagerbestand(**lager_id→lager, artikel_nr→artikel**, bestand, mindestbestand)

# 5. Übungsaufgaben

1. Ergänzen Sie das ERM um den Entitätstyp **Lieferant**. Ein Artikel kann von mehreren
   Lieferanten bezogen werden, jeder mit eigenem Einkaufspreis. Welche Tabelle entsteht?
2. Begründen Sie, warum lieferdatum nicht in auftragsposition gehört.
3. Zeichnen Sie die Beziehung Mitarbeiter – Lager einmal als 1:1 und einmal als 1:n.
   Welche Variante passt zu Halle 4, die zwei Schichtleiter bekommt?
"""

NORMALISIERUNG_FOLIEN = """
## Warum normalisieren?
- Redundanz vermeiden: jede Information nur einmal speichern
- Anomalien verhindern: Einfüge-, Änderungs- und Löschanomalie
- Beispiel: die alte Nordlicht-Excel-Liste mit Kundenadresse in jeder Auftragszeile
Notiz: Mit der echten Excel-Liste aus dem Lastenheft einsteigen – die Klasse findet die Anomalien selbst.

## Die Ausgangstabelle
| AuftragNr | Datum | Kunde | Ort | Artikel |
|---|---|---|---|---|
| 5001 | 02.11.2026 | Hafenbau Wulff KG | Hamburg | NL-04711 Palettenregal (4), NL-00815 Stretchfolie (20) |
| 5002 | 02.11.2026 | Agrarhandel Meyer | Stade | NL-00815 Stretchfolie (6) |

## 1. Normalform (1NF)
- Alle Attributwerte sind atomar
- Keine Wiederholungsgruppen in einer Zelle
- Lösung: eine Zeile pro Auftrag und Artikel, Artikelnummer und Menge in eigene Spalten

## 2. Normalform (2NF)
- 1NF ist erfüllt
- Jedes Nichtschlüsselattribut hängt vom **gesamten** Primärschlüssel ab
- Bezeichnung hängt nur von ArtikelNr ab, nicht von (AuftragNr, ArtikelNr) → eigene Tabelle Artikel
Notiz: Typischer Klausurfehler: 2NF bei einem einfachen (nicht zusammengesetzten) Schlüssel prüfen wollen – dann ist 2NF automatisch erfüllt.

## 3. Normalform (3NF)
- 2NF ist erfüllt
- Keine transitiven Abhängigkeiten: Nichtschlüssel hängt nicht von Nichtschlüssel ab
- Ort hängt von PLZ ab, PLZ von Kunde → Ortstabelle oder bewusst denormalisiert lassen
Notiz: Bei Nordlicht lassen wir PLZ/Ort aus Praxisgründen im Kunden – das muss in der Doku begründet werden!

## Zusammenfassung
| Normalform | Prüffrage |
|---|---|
| 1NF | Steht in jeder Zelle genau ein Wert? |
| 2NF | Hängt alles vom ganzen Schlüssel ab? |
| 3NF | Hängt alles nur vom Schlüssel ab? |
"""

SCHEMA_SQL = """-- NLV – Nordlicht Lagerverwaltung
-- Referenzschema LF5, FIAE 25a, Stand 09.11.2026 (Dr. Albers)
-- MariaDB 11 / MySQL 8, Zeichensatz utf8mb4

DROP DATABASE IF EXISTS nlv;
CREATE DATABASE nlv CHARACTER SET utf8mb4 COLLATE utf8mb4_german2_ci;
USE nlv;

CREATE TABLE kategorie (
  kategorie_id   INT PRIMARY KEY AUTO_INCREMENT,
  name           VARCHAR(60) NOT NULL UNIQUE
);

CREATE TABLE artikel (
  artikel_nr     CHAR(8) PRIMARY KEY,            -- Format NL-##### (Lastenheft F02)
  bezeichnung    VARCHAR(120) NOT NULL,
  gewicht_kg     DECIMAL(8,2) NOT NULL CHECK (gewicht_kg >= 0),
  einzelpreis    DECIMAL(10,2) NOT NULL,
  kategorie_id   INT NOT NULL,
  gesperrt       BOOLEAN NOT NULL DEFAULT FALSE,  -- F01: sperren statt löschen
  FOREIGN KEY (kategorie_id) REFERENCES kategorie(kategorie_id)
);

CREATE TABLE kunde (
  kunde_id        INT PRIMARY KEY AUTO_INCREMENT,
  firmenname      VARCHAR(120) NOT NULL,
  ansprechpartner VARCHAR(80),
  plz             CHAR(5) NOT NULL,
  ort             VARCHAR(60) NOT NULL,
  kunde_seit      DATE NOT NULL
);

CREATE TABLE lager (
  lager_id     INT PRIMARY KEY,
  standort     VARCHAR(60) NOT NULL,
  flaeche_qm   INT NOT NULL
);

CREATE TABLE auftrag (
  auftrag_id     INT PRIMARY KEY,
  kunde_id       INT NOT NULL,
  lager_id       INT NOT NULL,
  auftragsdatum  DATE NOT NULL,
  lieferdatum    DATE,
  status         ENUM('offen','kommissioniert','versendet','storniert') NOT NULL DEFAULT 'offen',
  FOREIGN KEY (kunde_id) REFERENCES kunde(kunde_id),
  FOREIGN KEY (lager_id) REFERENCES lager(lager_id)
);

CREATE TABLE auftragsposition (
  auftrag_id      INT,
  artikel_nr      CHAR(8),
  menge           INT NOT NULL CHECK (menge > 0),
  rabatt_prozent  DECIMAL(4,1) NOT NULL DEFAULT 0,
  PRIMARY KEY (auftrag_id, artikel_nr),
  FOREIGN KEY (auftrag_id) REFERENCES auftrag(auftrag_id) ON DELETE CASCADE,
  FOREIGN KEY (artikel_nr) REFERENCES artikel(artikel_nr)
);

CREATE TABLE lagerbestand (
  lager_id        INT,
  artikel_nr      CHAR(8),
  bestand         INT NOT NULL CHECK (bestand >= 0),   -- F04: nie negativ
  mindestbestand  INT NOT NULL DEFAULT 0,
  PRIMARY KEY (lager_id, artikel_nr),
  FOREIGN KEY (lager_id) REFERENCES lager(lager_id),
  FOREIGN KEY (artikel_nr) REFERENCES artikel(artikel_nr)
);

INSERT INTO kategorie (name) VALUES ('Regale'), ('Verpackung'), ('Fördertechnik'), ('Arbeitsschutz');

INSERT INTO lager VALUES
  (1, 'Halle 1 Billbrook', 4200), (2, 'Halle 2 Billbrook', 3800),
  (3, 'Halle 3 Billbrook', 5100), (4, 'Halle 4 Billbrook (ab 03/2027)', 6400);

INSERT INTO artikel VALUES
  ('NL-04711', 'Palettenregal 2,4 m verzinkt', 38.50, 129.00, 1, FALSE),
  ('NL-00815', 'Stretchfolie 500 mm x 300 m', 2.10, 11.90, 2, FALSE),
  ('NL-02200', 'Hubwagen 2,5 t', 74.00, 389.00, 3, FALSE),
  ('NL-03141', 'Sicherheitsschuhe S3 Gr. 43', 1.40, 64.50, 4, FALSE),
  ('NL-01234', 'Kartonage 600x400x400', 0.90, 1.85, 2, FALSE),
  ('NL-09999', 'Rollenbahn 1 m (Auslauf)', 22.00, 210.00, 3, TRUE);

INSERT INTO kunde (firmenname, ansprechpartner, plz, ort, kunde_seit) VALUES
  ('Hafenbau Wulff KG', 'Jens Wulff', '20457', 'Hamburg', '2014-03-01'),
  ('Agrarhandel Meyer', 'Anke Meyer', '21680', 'Stade', '2019-07-15'),
  ('Elbe Getränke GmbH', 'Murat Aydin', '21029', 'Hamburg', '2021-01-11'),
  ('Werft Kuddel & Söhne', NULL, '25541', 'Brunsbüttel', '2023-09-04');

INSERT INTO auftrag VALUES
  (5001, 1, 1, '2026-11-02', '2026-11-04', 'versendet'),
  (5002, 2, 1, '2026-11-02', NULL, 'offen'),
  (5003, 3, 2, '2026-11-03', '2026-11-05', 'kommissioniert'),
  (5004, 1, 3, '2026-11-05', NULL, 'offen');

INSERT INTO auftragsposition VALUES
  (5001, 'NL-04711', 4, 0), (5001, 'NL-00815', 20, 5.0),
  (5002, 'NL-00815', 6, 0), (5003, 'NL-02200', 1, 0),
  (5003, 'NL-03141', 12, 10.0), (5004, 'NL-01234', 500, 12.5);

INSERT INTO lagerbestand VALUES
  (1, 'NL-04711', 36, 20), (1, 'NL-00815', 140, 200), (2, 'NL-02200', 3, 2),
  (2, 'NL-03141', 25, 30), (3, 'NL-01234', 4200, 1000);
"""

SQL_BOOK = [
    (
        "Kapitel 1: SELECT und FROM",
        """
Jede Abfrage beginnt mit **SELECT** (welche Spalten) und **FROM** (aus welcher Tabelle).

```
SELECT artikel_nr, bezeichnung, einzelpreis
FROM artikel;
```

Mit `SELECT *` werden alle Spalten ausgegeben. In Klausuren und im Projekt bitte immer die
Spalten ausdrücklich nennen – das ist lesbarer und robuster gegen Schemaänderungen.

Spalten können mit **AS** umbenannt und berechnet werden:

```
SELECT bezeichnung, einzelpreis * 1.19 AS brutto
FROM artikel;
```
""",
    ),
    (
        "Kapitel 2: WHERE – Zeilen filtern",
        """
WHERE filtert Zeilen nach Bedingungen. Vergleichsoperatoren: =, <>, <, >, <=, >=.

| Operator | Beispiel | Bedeutung |
|---|---|---|
| BETWEEN | einzelpreis BETWEEN 10 AND 100 | einschließlich der Grenzen |
| IN | status IN ('offen', 'kommissioniert') | einer von mehreren Werten |
| LIKE | bezeichnung LIKE 'Palette%' | % = beliebig viele Zeichen, _ = genau eins |
| IS NULL | lieferdatum IS NULL | Wert fehlt (nicht = NULL!) |

> Häufiger Fehler: `WHERE lieferdatum = NULL` liefert nie eine Zeile. Richtig ist IS NULL.

Bedingungen werden mit AND, OR und NOT verknüpft; AND bindet stärker als OR – im Zweifel
Klammern setzen.
""",
    ),
    (
        "Kapitel 3: ORDER BY und LIMIT",
        """
ORDER BY sortiert das Ergebnis aufsteigend (ASC, Standard) oder absteigend (DESC).
Mehrere Sortierkriterien werden mit Komma getrennt.

```
SELECT firmenname, ort, kunde_seit
FROM kunde
ORDER BY ort ASC, kunde_seit DESC;
```

LIMIT begrenzt die Anzahl der Zeilen, z. B. die drei teuersten Artikel:
`SELECT bezeichnung, einzelpreis FROM artikel ORDER BY einzelpreis DESC LIMIT 3;`
""",
    ),
    (
        "Kapitel 4: Aggregatfunktionen",
        """
Aggregatfunktionen fassen viele Zeilen zu einem Wert zusammen: **COUNT, SUM, AVG, MIN, MAX**.

```
SELECT COUNT(*) AS anzahl_artikel, AVG(einzelpreis) AS durchschnittspreis
FROM artikel
WHERE gesperrt = FALSE;
```

COUNT(*) zählt Zeilen, COUNT(spalte) zählt nur Werte, die nicht NULL sind. Für Nordlicht
liefert `SELECT COUNT(lieferdatum) FROM auftrag;` deshalb 2 und nicht 4.
""",
    ),
    (
        "Kapitel 5: GROUP BY und HAVING",
        """
GROUP BY bildet Gruppen, über die aggregiert wird. Jede Spalte im SELECT muss entweder in
GROUP BY stehen oder in einer Aggregatfunktion.

```
SELECT auftrag_id, SUM(menge) AS stueck
FROM auftragsposition
GROUP BY auftrag_id
HAVING SUM(menge) > 10;
```

**WHERE** filtert Zeilen *vor* dem Gruppieren, **HAVING** filtert Gruppen *danach*.
Merkhilfe für die Reihenfolge der Ausführung: FROM → WHERE → GROUP BY → HAVING →
SELECT → ORDER BY → LIMIT.
""",
    ),
    (
        "Kapitel 6: Daten ändern (INSERT, UPDATE, DELETE)",
        """
```
INSERT INTO kategorie (name) VALUES ('Etiketten');
UPDATE artikel SET einzelpreis = 12.40 WHERE artikel_nr = 'NL-00815';
DELETE FROM auftrag WHERE auftrag_id = 5004;
```

> Vor jedem UPDATE oder DELETE erst die WHERE-Bedingung mit einem SELECT testen! Ein
> UPDATE ohne WHERE ändert jede Zeile der Tabelle.

Bei Nordlicht werden Artikel laut Lastenheft (F01) nie gelöscht, sondern gesperrt:
`UPDATE artikel SET gesperrt = TRUE WHERE artikel_nr = 'NL-09999';`
""",
    ),
]

JOINS = """
# JOINs – Tabellen verbinden

Ein JOIN verbindet Zeilen zweier Tabellen über eine Bedingung, meistens Fremdschlüssel =
Primärschlüssel.

## INNER JOIN

Nur Zeilen mit Partner auf beiden Seiten:

```
SELECT a.auftrag_id, k.firmenname, a.status
FROM auftrag a
INNER JOIN kunde k ON a.kunde_id = k.kunde_id;
```

## LEFT JOIN

Alle Zeilen der linken Tabelle, rechts NULL, wenn es keinen Partner gibt. Damit finden wir
z. B. Kunden ohne Auftrag:

```
SELECT k.firmenname
FROM kunde k
LEFT JOIN auftrag a ON a.kunde_id = k.kunde_id
WHERE a.auftrag_id IS NULL;
```

Ergebnis mit den Testdaten: **Werft Kuddel & Söhne**.

## Mehrere JOINs

```
SELECT a.auftrag_id, k.firmenname, ar.bezeichnung, p.menge
FROM auftrag a
JOIN kunde k            ON k.kunde_id = a.kunde_id
JOIN auftragsposition p ON p.auftrag_id = a.auftrag_id
JOIN artikel ar         ON ar.artikel_nr = p.artikel_nr
ORDER BY a.auftrag_id;
```

---

## Aufgaben

1. Geben Sie alle offenen Aufträge mit Firmenname und Lagerstandort aus.
2. Welche Artikel liegen unter ihrem Mindestbestand? (Nachbestellliste, Lastenheft F05)
3. Berechnen Sie den Auftragswert jedes Auftrags unter Berücksichtigung des Rabatts.
4. Welcher Kunde hat den höchsten Gesamtumsatz?
5. Zählen Sie pro Lager die Anzahl unterschiedlicher Artikel. Lager ohne Bestand sollen mit
   0 erscheinen.
"""

PROBEKLAUSUR = f"""
Bearbeitungszeit 90 Minuten, keine Hilfsmittel. Verwenden Sie das Nordlicht-Schema
(Anlage). Gesamtpunktzahl: 50.

# Aufgabe 1: Modellierung (12 Punkte)

Ein Kunde kann mehrere Lieferadressen haben, eine Lieferadresse gehört zu genau einem
Kunden. Jeder Auftrag wird an genau eine Lieferadresse geliefert.

a) Ergänzen Sie das ERM um den Entitätstyp Lieferadresse mit Kardinalitäten. (6 P.)
b) Geben Sie das geänderte Relationenschema an. (6 P.)

# Aufgabe 2: Normalisierung (10 Punkte)

Gegeben ist die Tabelle schulung(mitarbeiter_nr, name, kurs_nr, kurstitel, datum, raum).
Ein Mitarbeiter kann mehrere Kurse besuchen; Kurstitel hängt nur von kurs_nr ab.
Überführen Sie die Tabelle in die 3. Normalform und begründen Sie jeden Schritt.

# Aufgabe 3: SQL (28 Punkte)

a) Alle Artikel der Kategorie „Verpackung“ alphabetisch sortiert. (4 P.)
b) Anzahl der Aufträge je Status. (5 P.)
c) Alle Kunden aus Hamburg mit der Summe ihrer bestellten Stückzahlen. (7 P.)
d) Artikel, die noch nie bestellt wurden. (6 P.)
e) Erhöhen Sie den Mindestbestand aller Artikel der Kategorie „Arbeitsschutz“ in Lager 2
   um 10 %. (6 P.)

Die echte Klausur findet am {DATES["lf5_klausur"]} statt und ist genauso aufgebaut.
"""

LOESUNG = """
# Lösung Aufgabe 3

a)
```
SELECT a.bezeichnung FROM artikel a
JOIN kategorie k ON k.kategorie_id = a.kategorie_id
WHERE k.name = 'Verpackung' ORDER BY a.bezeichnung;
```
b) `SELECT status, COUNT(*) FROM auftrag GROUP BY status;`

c)
```
SELECT k.firmenname, SUM(p.menge) AS stueck
FROM kunde k JOIN auftrag a ON a.kunde_id = k.kunde_id
JOIN auftragsposition p ON p.auftrag_id = a.auftrag_id
WHERE k.ort = 'Hamburg' GROUP BY k.kunde_id, k.firmenname;
```
Erwartung mit Testdaten: Hafenbau Wulff KG 524, Elbe Getränke GmbH 13.

d) `SELECT bezeichnung FROM artikel WHERE artikel_nr NOT IN (SELECT artikel_nr FROM auftragsposition);`
Erwartung: Rollenbahn 1 m (Auslauf).

e)
```
UPDATE lagerbestand SET mindestbestand = CEIL(mindestbestand * 1.1)
WHERE lager_id = 2 AND artikel_nr IN (
  SELECT artikel_nr FROM artikel WHERE kategorie_id = 4);
```
Punkte: Teillösungen werden anerkannt; Syntaxfehler kosten je 1 Punkt.
"""


def build(ds: Dataset) -> None:
    c = ds.course(
        "lf5",
        "LF5 – Software zur Verwaltung von Daten anpassen (Datenbanken)",
        {"teacher": "editingteacher", "student": "student"},
    )

    s = c.section("Allgemeines")
    s.page(
        "kursinfo",
        "Kursinformationen und Leistungsbewertung",
        f"""
Lehrkraft: {AUTHOR}. Unterricht montags 1.–2. und mittwochs 5.–6. Stunde in Raum 2.14.

## Leistungsbewertung

| Bestandteil | Anteil |
|---|---|
| Klausur SQL am {DATES["lf5_klausur"]} | 40 % |
| Lernfeldprojekt NLV (Datenmodell + SQL-Skript) | 30 % |
| Mitarbeit und Übungen | 30 % |

## Software

Wir arbeiten mit **MariaDB 11** und dem Client **HeidiSQL** (Labor) bzw. DBeaver (eigene
Geräte). Das Referenzschema „nlv“ liegt im Abschnitt SQL-Grundlagen.
""",
    )
    s.activity(
        "forum",
        "fragen",
        "Fragen zum Unterricht",
        "Fragen zu Übungen und SQL-Fehlermeldungen. Bitte die Fehlermeldung vollständig "
        "kopieren und die Abfrage dazuschreiben.",
    )
    s.url(
        "hackmd",
        "Gemeinsame Mitschrift (HackMD)",
        URLS["hackmd_lf5"],
        intro="Die Klasse schreibt hier abwechselnd mit. Jede Woche zwei Protokollanten.",
    )

    s = c.section("Thema 1: Datenmodellierung mit dem ERM")
    s.resource(
        "skript_erm",
        "Skript: Entity-Relationship-Modell",
        "LF5_Skript_ERM.pdf",
        render.to_pdf(
            "Skript: Das Entity-Relationship-Modell",
            ERM_SKRIPT,
            header=f"{HEADER} · LF5",
            author=AUTHOR,
        ),
    )
    s.page(
        "erm_nordlicht",
        "ERM der Nordlicht-Lagerverwaltung",
        """
Das Diagramm zeigt den aktuellen Entwurf aus dem Unterricht vom 04.11.2026. Beantworten
Sie bis Mittwoch: Warum hat die Tabelle Lagerbestand einen zusammengesetzten
Primärschlüssel, und was würde passieren, wenn bestand direkt in Artikel stünde?

> Tipp: Vergleichen Sie mit Regel 3 im Skript (m:n-Beziehungen).
""",
        images={"erm_nordlicht_v3.png": images.er_diagram()},
        raw_html='<p><img src="@@PLUGINFILE@@/erm_nordlicht_v3.png" alt="ERM Lagerverwaltung '
        "Nordlicht mit Kunde, Auftrag, Artikel, Auftragsposition, Lager und "
        'Lagerbestand" width="900"></p>',
    )
    s.resource(
        "normalisierung",
        "Folien: Normalisierung (1NF–3NF)",
        "LF5_Normalisierung.pptx",
        render.to_pptx(
            "Normalisierung", NORMALISIERUNG_FOLIEN, subtitle=f"LF5 · {CLASS} · {AUTHOR}"
        ),
    )
    s.resource(
        "ab_normalisierung",
        "Arbeitsblatt Normalisierung",
        "AB_Normalisierung.doc",
        render.to_docx(
            "Arbeitsblatt: Normalisierung der Schulungsliste",
            """
Die Personalabteilung von Nordlicht führt Schulungen in einer einzigen Tabelle:

| MitarbeiterNr | Name | Abteilung | Abteilungsleiter | Schulungen |
|---|---|---|---|---|
| 1007 | Kaya, Emre | Lager | Hr. Brandes | Staplerschein (12.03.2026), Ersthelfer (03.06.2026) |
| 1012 | Wolff, Jana | Disposition | Fr. Lindqvist | SAP-Grundlagen (20.01.2026) |
| 1019 | Petrov, Ivan | Lager | Hr. Brandes | Staplerschein (12.03.2026) |

1. Nennen Sie je ein Beispiel für eine Einfüge-, Änderungs- und Löschanomalie.
2. Bringen Sie die Tabelle in die 1NF.
3. Bestimmen Sie den Primärschlüssel und bringen Sie die Tabelle in die 2NF und 3NF.
4. Zeichnen Sie das zugehörige ERM.
""",
            author=AUTHOR,
        ),
        intro="Achtung: Die Datei heißt .doc, ist aber ein Word-2007-Dokument.",
    )
    s.url(
        "yt_normalisierung",
        "Video: Database Normalization (englisch, 28 min)",
        URLS["youtube_lf5"],
        intro="Freiwilliges Zusatzvideo mit vielen Beispielen bis zur 5NF. Für uns relevant "
        "sind 1NF bis 3NF.",
    )

    s = c.section("Thema 2: SQL-Grundlagen")
    s.book(
        "sql_handbuch",
        "SQL-Handbuch der Klasse",
        SQL_BOOK,
        intro="Nachschlagewerk zu allen SQL-Befehlen, die in der Klausur vorkommen.",
    )
    s.resource(
        "schema",
        "Referenzschema nlv (SQL-Skript)",
        "nlv_schema.sql",
        SCHEMA_SQL.encode("utf-8"),
        intro="Mit `SOURCE nlv_schema.sql;` in MariaDB einspielen. Das Skript löscht "
        "eine vorhandene Datenbank nlv!",
    )
    s.folder(
        "uebungen",
        "Übungen SQL",
        [
            (
                "/Woche 45/",
                "Uebung_01_SELECT.md",
                render.to_markdown(
                    "Übung 1: SELECT",
                    """
1. Alle Artikel mit Bezeichnung und Einzelpreis.
2. Alle Kunden aus Hamburg, sortiert nach Firmenname.
3. Alle Aufträge ohne Lieferdatum.
4. Die drei schwersten Artikel.
""",
                ),
            ),
            (
                "/Woche 45/",
                "Uebung_01_Loesung.sql",
                b"""-- Loesung Uebung 1
SELECT bezeichnung, einzelpreis FROM artikel;
SELECT firmenname FROM kunde WHERE ort = 'Hamburg' ORDER BY firmenname;
SELECT auftrag_id FROM auftrag WHERE lieferdatum IS NULL;
SELECT bezeichnung, gewicht_kg FROM artikel ORDER BY gewicht_kg DESC LIMIT 3;
""",
            ),
            (
                "/Woche 46/",
                "Uebung_02_Aggregate.md",
                render.to_markdown(
                    "Übung 2: Aggregatfunktionen",
                    """
1. Wie viele Artikel sind gesperrt?
2. Durchschnittlicher Einzelpreis je Kategorie-ID.
3. Gesamtbestand je Lager, nur Lager mit mehr als 100 Stück.
""",
                ),
            ),
            (
                "/Woche 46/",
                "nlv_artikel_export.csv",
                render.to_csv(
                    [
                        ["artikel_nr", "bezeichnung", "gewicht_kg", "einzelpreis", "kategorie"],
                        ["NL-04711", "Palettenregal 2,4 m verzinkt", 38.5, 129.0, "Regale"],
                        ["NL-00815", "Stretchfolie 500 mm x 300 m", 2.1, 11.9, "Verpackung"],
                        ["NL-02200", "Hubwagen 2,5 t", 74.0, 389.0, "Fördertechnik"],
                        ["NL-03141", "Sicherheitsschuhe S3 Gr. 43", 1.4, 64.5, "Arbeitsschutz"],
                        ["NL-01234", "Kartonage 600x400x400", 0.9, 1.85, "Verpackung"],
                    ]
                ),
            ),
        ],
        intro="Jede Woche ein neuer Unterordner. Lösungen erscheinen jeweils am Mittwoch.",
    )

    s = c.section("Thema 3: Abfragen über mehrere Tabellen (JOINs)")
    s.resource(
        "joins",
        "Arbeitsblatt JOINs",
        "LF5_AB_JOINs.pdf",
        render.to_pdf("Arbeitsblatt: JOINs", JOINS, header=f"{HEADER} · LF5", author=AUTHOR),
    )
    s.activity(
        "quiz",
        "quiz_joins",
        "Selbsttest JOINs",
        """
10 Fragen zu INNER JOIN, LEFT JOIN und GROUP BY. Beliebig viele Versuche, zählt nicht zur
Note. Ideal zur Vorbereitung auf die Klausur.
""",
    )

    s = c.section("Klausurvorbereitung")
    s.page(
        "klausurthemen",
        "Themen der Klausur",
        f"""
Die Klausur am **{DATES["lf5_klausur"]}** (1.–2. Stunde, Raum 2.14, 90 Minuten) umfasst:

- ERM mit Kardinalitäten und Überführung ins Relationenmodell
- Normalisierung bis zur 3NF mit Begründung
- SELECT mit WHERE, ORDER BY, Aggregatfunktionen, GROUP BY/HAVING
- INNER JOIN und LEFT JOIN über bis zu vier Tabellen
- INSERT, UPDATE, DELETE

Nicht klausurrelevant: Unterabfragen mit EXISTS, Views, Trigger. Hilfsmittel sind nicht
erlaubt. Nachschreibtermin für entschuldigt Fehlende: Mittwoch, 16.12.2026, 8. Stunde.
""",
    )
    s.resource(
        "probeklausur",
        "Probeklausur LF5",
        "LF5_Probeklausur.pdf",
        render.to_pdf(
            "Probeklausur LF5 – Datenbanken", PROBEKLAUSUR, header=f"{HEADER} · LF5", author=AUTHOR
        ),
    )
    s.resource(
        "loesung",
        "Lösung Probeklausur",
        "LF5_Probeklausur_Loesung.pdf",
        render.to_pdf("Lösung Probeklausur LF5", LOESUNG, header=f"{HEADER} · LF5", author=AUTHOR),
        intro="Wird nach der Besprechung am 02.12.2026 freigeschaltet.",
        visible=False,
    )

    s = c.section("Thema 4: Transaktionen und Rechte (ab Januar)", available_from=NEVER)
    s.page(
        "transaktionen",
        "Transaktionen",
        """
Eine Transaktion fasst mehrere Anweisungen zu einer unteilbaren Einheit zusammen
(ACID: Atomarität, Konsistenz, Isolation, Dauerhaftigkeit). Bei Nordlicht muss eine
Umlagerung von Halle 1 nach Halle 4 entweder ganz oder gar nicht gebucht werden:
START TRANSACTION; UPDATE … ; UPDATE … ; COMMIT;
""",
    )

    ds.probe(
        "Wie finde ich Kunden ohne Auftrag?",
        "LEFT JOIN … WHERE a.auftrag_id IS NULL; Ergebnis Werft Kuddel & Söhne",
        "LF5_AB_JOINs.pdf",
    )
    ds.probe(
        "Was ist der Unterschied zwischen WHERE und HAVING?",
        "WHERE filtert Zeilen vor, HAVING Gruppen nach dem Gruppieren",
        "SQL-Handbuch, Kapitel 5 (Buch)",
    )
    ds.probe(
        "Wie lautet die Lösung zu Aufgabe 3d der Probeklausur?",
        "NOT IN (SELECT artikel_nr FROM auftragsposition) – nur teacher (Lösung verborgen)",
        "LF5_Probeklausur_Loesung.pdf (verborgen)",
        "nur teacher",
    )
    ds.probe(
        "Welche Artikel liegen unter Mindestbestand?",
        "NL-00815 in Lager 1 (140 < 200), NL-03141 in Lager 2 (25 < 30)",
        "nlv_schema.sql",
    )
    ds.probe(
        "Was ist die 2. Normalform?",
        "alle Nichtschlüsselattribute hängen vom gesamten Schlüssel ab",
        "LF5_Normalisierung.pptx (Folie 4) + Notiz",
    )
    ds.probe(
        "Was ist in LF5 bis zum 18.11. zu erledigen?",
        "Aufgaben 5 und 6 des Arbeitsblatts JOINs und M1 (ERM + Relationenschema) vorbereiten",
        "Gemeinsame Mitschrift (HackMD, extern)",
        "student, teacher",
    )
