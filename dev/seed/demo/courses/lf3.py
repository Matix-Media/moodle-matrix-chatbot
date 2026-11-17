"""LF3 Netzwerke: OSI, IPv4, subnetting, the network plan for Nordlicht hall 4."""

from __future__ import annotations

import ipaddress

from .. import images, render
from ..external import URLS
from ..model import NEVER, Dataset
from ..school import CLASS, COMPANY, DATES, HEADER, teacher

AUTHOR = teacher("PET")

OSI_FOLIEN = """
## Warum ein Schichtenmodell?
- Geräte verschiedener Hersteller sollen miteinander kommunizieren
- Jede Schicht löst ein Teilproblem und nutzt nur die Dienste der Schicht darunter
- Austauschbarkeit: WLAN statt Kabel ändert nichts an HTTP
Notiz: Einstieg mit der Frage: Was passiert alles, wenn ihr im Browser moodle aufruft?

## Die 7 Schichten
| Nr. | Schicht | Englisch | Beispiele | PDU |
|---|---|---|---|---|
| 7 | Anwendung | Application | HTTP, DNS, SMTP | Daten |
| 6 | Darstellung | Presentation | TLS, JPEG, UTF-8 | Daten |
| 5 | Sitzung | Session | RPC, NetBIOS | Daten |
| 4 | Transport | Transport | TCP, UDP | Segment |
| 3 | Vermittlung | Network | IPv4, IPv6, ICMP | Paket |
| 2 | Sicherung | Data Link | Ethernet, WLAN 802.11, ARP | Frame |
| 1 | Bitübertragung | Physical | Kabel Cat 6, Glasfaser, Funk | Bit |

## Eselsbrücke
- Von unten nach oben: „Please Do Not Throw Salami Pizza Away“
- Deutsch von oben: „Alle Deutschen Studenten Trinken Verschiedene Sorten Bier“
Notiz: Die deutsche Eselsbrücke nur mündlich erwähnen, steht so nicht im Lehrbuch.

## Kapselung
- Jede Schicht fügt ihren Header hinzu (Encapsulation)
- Ethernet-Frame | IP-Header | TCP-Header | HTTP-Daten | FCS
- Der Empfänger packt in umgekehrter Reihenfolge aus

## Geräte und Schichten
| Gerät | Schicht | Arbeitet mit |
|---|---|---|
| Hub / Repeater | 1 | Bits |
| Switch | 2 | MAC-Adressen |
| Router | 3 | IP-Adressen |
| Firewall (Paketfilter) | 3–4 | IP, Ports |
"""

SUBNETTING = f"""
# 1. Aufbau einer IPv4-Adresse

Eine IPv4-Adresse besteht aus 32 Bit, geschrieben als vier Dezimalzahlen (Oktette), z. B.
**192.168.20.17**. Die Subnetzmaske legt fest, welche Bits zum **Netzanteil** und welche
zum **Hostanteil** gehören. In CIDR-Schreibweise gibt /24 die Anzahl der Netzbits an.

| CIDR | Maske | Hosts je Netz |
|---|---|---|
| /24 | 255.255.255.0 | 254 |
| /25 | 255.255.255.128 | 126 |
| /26 | 255.255.255.192 | 62 |
| /27 | 255.255.255.224 | 30 |
| /28 | 255.255.255.240 | 14 |
| /29 | 255.255.255.248 | 6 |
| /30 | 255.255.255.252 | 2 |

Formel: nutzbare Hosts = 2^(32 − Präfix) − 2 (Netz- und Broadcastadresse abziehen).

# 2. Netzadresse berechnen

Die Netzadresse erhält man durch bitweises **UND** von Adresse und Maske.

Beispiel 10.40.20.77/25: Das letzte Oktett 77 = 0100 1101, Maske 128 = 1000 0000,
UND ergibt 0000 0000 → Netzadresse **10.40.20.0**, Broadcast **10.40.20.127**, Hosts
10.40.20.1 bis 10.40.20.126.

---

# 3. Subnetting mit fester Maske

Ein /24 soll in vier gleich große Netze geteilt werden. Für 4 Netze braucht man 2 zusätzliche
Netzbits (2² = 4), also /26 mit je 62 Hosts. Die Schrittweite (Blockgröße) im letzten
Oktett ist 256 − 192 = **64**: Netze bei .0, .64, .128, .192.

# 4. VLSM – Subnetting mit variabler Maske

Bei VLSM bekommt jedes Netz nur so viele Adressen, wie es braucht. Man beginnt immer mit
dem **größten** Netz, damit die Blöcke sauber ausgerichtet bleiben.

# 5. Anwendung: Halle 4 bei {COMPANY}

Für die neue Halle steht der Bereich **10.40.0.0/16** zur Verfügung. Geplant sind (siehe
Netzplan): Büro 18 PCs + 2 Drucker + Reserve 50 %, Lager 60 Handscanner + Reserve,
24 Kameras, Management bis 12 Geräte.

| VLAN | Zweck | Bedarf | Netz | Gateway |
|---|---|---|---|---|
| 10 | Büro | 30 | 10.40.10.0/26 | 10.40.10.1 |
| 20 | Lager | 90 | 10.40.20.0/25 | 10.40.20.1 |
| 30 | Kameras | 24 | 10.40.30.0/27 | 10.40.30.1 |
| 99 | Management | 12 | 10.40.99.0/28 | 10.40.99.1 |

> Das dritte Oktett entspricht bewusst der VLAN-Nummer. Das verschwendet Adressen, macht
> die Fehlersuche aber erheblich leichter – in einem /16 ist genug Platz.

# 6. Aufgaben

1. Berechnen Sie Netzadresse, Broadcast und Hostbereich für 172.16.45.200/27.
2. Teilen Sie 192.168.50.0/24 in 8 gleich große Netze. Geben Sie alle Netze an.
3. VLSM: 192.168.100.0/24 für Netze mit 100, 50, 20 und 2 Hosts.
4. Wie viele Handscanner passen in VLAN 20, wenn 10 Adressen für Access Points reserviert
   sind?
5. Warum ist /31 im Büro-VLAN ungeeignet?
6. Prüfen Sie den Netzplan von Halle 4: überschneiden sich zwei Netze?
"""

LEHRBUCH_KOPIE = """
# 4.3 Das Address Resolution Protocol (ARP)

Will ein Rechner ein IP-Paket an einen Host im selben Netz senden, benötigt er dessen
MAC-Adresse, denn auf Schicht 2 wird nur mit MAC-Adressen zugestellt. Dazu sendet er eine
ARP-Anfrage als Broadcast an ff:ff:ff:ff:ff:ff: „Wer hat 192.168.1.20? Antwort an
192.168.1.10.“ Nur der Host mit dieser IP-Adresse antwortet per Unicast mit seiner
MAC-Adresse.

Die Zuordnung wird im ARP-Cache gespeichert, unter Windows anzeigen mit arp -a, unter
Linux mit ip neigh. Einträge verfallen nach wenigen Minuten.

Liegt das Ziel in einem anderen Netz, fragt der Rechner nicht nach der Zieladresse,
sondern nach der MAC-Adresse seines **Standardgateways**.

# 4.4 Sicherheit

ARP kennt keine Authentifizierung. Ein Angreifer im selben Netz kann gefälschte Antworten
senden (ARP-Spoofing) und so Verkehr über sich umleiten. Gegenmaßnahmen sind
Dynamic ARP Inspection auf dem Switch und die Trennung von Netzen durch VLANs.

Aufgaben S. 112: 1. Erklären Sie, warum die ARP-Anfrage ein Broadcast ist. 2. Welche
MAC-Adresse steht im Frame an einen Webserver im Internet?
"""


def subnet_table() -> list[list[object]]:
    rows: list[list[object]] = [["Präfix", "Maske", "Blockgröße", "Hosts", "Netze in /24"]]
    for prefix in range(24, 31):
        net = ipaddress.ip_network(f"0.0.0.0/{prefix}")
        rows.append(
            [
                f"/{prefix}",
                str(net.netmask),
                2 ** (32 - prefix),
                2 ** (32 - prefix) - 2,
                2 ** (prefix - 24),
            ]
        )
    return rows


def halle4_rows() -> list[list[object]]:
    rows: list[list[object]] = [
        ["VLAN", "Netz", "Erste Adresse", "Letzte Adresse", "Broadcast", "Hosts"]
    ]
    for vlan, cidr in (
        (10, "10.40.10.0/26"),
        (20, "10.40.20.0/25"),
        (30, "10.40.30.0/27"),
        (99, "10.40.99.0/28"),
    ):
        net = ipaddress.ip_network(cidr)
        hosts = list(net.hosts())
        rows.append(
            [vlan, cidr, str(hosts[0]), str(hosts[-1]), str(net.broadcast_address), len(hosts)]
        )
    return rows


def build(ds: Dataset) -> None:
    c = ds.course(
        "lf3",
        "LF3 – Clients in Netzwerke einbinden",
        {"teacher": "editingteacher", "student": "student", "student2": "student"},
    )

    s = c.section("Allgemeines")
    s.label(
        "info",
        f"""
**LF3 bei {AUTHOR}** – Montag 3.–4. Stunde und Mittwoch 7. Stunde im Labor 0.11. Bitte
bringen Sie zu jeder Laborstunde Ihren Laptop und ein Konsolenkabel (USB-Seriell) mit.
""",
    )
    s.activity(
        "forum",
        "forum",
        "Laborforum",
        "Fragen zu Laborübungen, Packet Tracer und "
        "Konfigurationen. Bitte Konfigurationen als Text einfügen, keine Handyfotos.",
    )

    s = c.section("Thema 1: Das OSI-Modell")
    s.resource(
        "osi",
        "Folien: Das OSI-Referenzmodell",
        "LF3_OSI_Modell.pptx",
        render.to_pptx("Das OSI-Referenzmodell", OSI_FOLIEN, subtitle=f"LF3 · {CLASS}"),
    )
    s.url(
        "yt_osi",
        "Erklärvideo: Das ISO/OSI-Referenzmodell",
        URLS["youtube_lf3"],
        intro="Kurzes deutsches Erklärvideo, gut zur Wiederholung vor dem Test.",
    )
    s.resource(
        "arp",
        "Lehrbuchauszug ARP (Kopie)",
        "Scan_Lehrbuch_S110-112.pdf",
        render.to_scanned_pdf(
            "Kapitel 4: Adressauflösung",
            LEHRBUCH_KOPIE,
            seed=3,
            header="IT-Berufe Grundstufe, 7. Auflage",
        ),
        intro="Eingescannte Seiten aus dem Lehrbuch, Aufgaben S. 112 bis Mittwoch.",
    )

    s = c.section("Thema 2: IPv4-Adressierung und Subnetting")
    s.resource(
        "subnetting",
        "Skript Subnetting",
        "LF3_Skript_Subnetting.pdf",
        render.to_pdf(
            "Skript: IPv4-Adressierung und Subnetting",
            SUBNETTING,
            header=f"{HEADER} · LF3",
            author=AUTHOR,
        ),
    )
    s.resource(
        "tabelle",
        "Subnetz-Tabelle (Excel 97)",
        "Subnetztabelle.xls",
        render.to_xls([("Präfixe", subnet_table()), ("Halle 4", halle4_rows())]),
        intro="Alte Excel-Datei aus der Sammlung der Fachgruppe; öffnet in jedem Tabellenprogramm.",
    )
    s.resource(
        "tafelbild",
        "Foto Tafelbild 11.11.2026",
        "Tafelbild_2026-11-11.jpg",
        images.whiteboard_photo(
            [
                "Subnetting - Rechenweg",
                "1) Hosts + Reserve -> 2^n - 2 >= Bedarf",
                "2) Praefix = 32 - n",
                "3) Blockgroesse = 256 - letztes Maskenoktett",
                "4) Netze immer mit dem GROESSTEN beginnen!",
                "Bsp: 90 Hosts -> n = 7 -> /25 -> Block 128",
                "Test am 25.11.: Subnetting + OSI (45 min)",
            ],
            seed=11,
        ),
        intro="Foto vom Whiteboard aus der letzten Stunde.",
    )
    s.page(
        "test",
        "Test am 25.11.2026",
        f"""
Der Test findet am **{DATES["lf3_test"]}** in der 3.–4. Stunde im Labor 0.11 statt
(45 Minuten). Themen: OSI-Modell (Schichten, Geräte, PDU), IPv4-Adressaufbau, Subnetting
mit fester Maske und VLSM. Ein nicht programmierbarer Taschenrechner ist erlaubt, die
Subnetz-Tabelle nicht.

Nachschreibtermin: Mittwoch, 02.12.2026, 8. Stunde in Raum 2.14.
""",
    )

    s = c.section(f"Thema 3: Netzplan Halle 4 ({COMPANY})")
    s.page(
        "netzplan",
        "Netzplan Halle 4",
        """
Grundlage für den LF3-Teil des Lernfeldprojekts. Der Netzplan zeigt die geplanten VLANs;
das Adresskonzept steht im Subnetting-Skript, Abschnitt 5.

## Ihre Aufgaben

- Prüfen Sie, ob die Größe jedes Netzes zum Bedarf passt (inklusive Reserve).
- Ergänzen Sie ein Gäste-WLAN (VLAN 40) für bis zu 25 Geräte.
- Begründen Sie, warum die Kameras ein eigenes VLAN bekommen.
""",
        images={"netzplan_halle4.png": images.network_plan()},
        raw_html='<p><img src="@@PLUGINFILE@@/netzplan_halle4.png" alt="Netzplan Halle 4 mit '
        'Router, Core-Switch und VLANs 10, 20, 30, 99" width="900"></p>',
    )
    s.folder(
        "labor",
        "Laborübungen",
        [
            (
                "/",
                "Laborprotokoll_Vorlage.docx",
                render.to_docx(
                    "Laborprotokoll",
                    """
| Feld | Eintrag |
|---|---|
| Übung | |
| Gruppe | |
| Datum | |

## Ziel der Übung

## Aufbau (Skizze oder Beschreibung)

## Durchführung und Befehle

## Ergebnis und Tests (ping, tracert)

## Probleme und Lösungen
""",
                    author=AUTHOR,
                ),
            ),
            (
                "/Uebung 3 VLAN/",
                "sw-h4-01_startup-config.txt",
                render.to_text(
                    "sw-h4-01 startup-config (Ausschnitt)",
                    """
```
hostname SW-H4-01
vlan 10
 name BUERO
vlan 20
 name LAGER
vlan 30
 name KAMERAS
vlan 99
 name MGMT
interface range GigabitEthernet1/0/1-18
 switchport mode access
 switchport access vlan 10
interface range GigabitEthernet1/0/19-22
 switchport mode access
 switchport access vlan 30
 power inline auto
interface GigabitEthernet1/0/24
 description Uplink Router
 switchport mode trunk
 switchport trunk allowed vlan 10,20,30,99
interface Vlan99
 ip address 10.40.99.2 255.255.255.240
ip default-gateway 10.40.99.1
```
""",
                ),
            ),
            (
                "/Uebung 3 VLAN/",
                "Aufgabe_VLAN.odt",
                render.to_odt(
                    "Übung 3: VLANs konfigurieren",
                    """
Konfigurieren Sie in Packet Tracer den Switch SW-H4-01 nach der Vorlage. Testen Sie, dass
ein PC in VLAN 10 einen PC in VLAN 20 erst nach Einrichtung des Routers (Router-on-a-Stick)
erreicht. Dokumentieren Sie alles im Laborprotokoll.
""",
                    author=AUTHOR,
                ),
            ),
            ("/Uebung 3 VLAN/", "Uebung3_halle4.pkt", b"\x00PT\x07binary packet tracer data"),
            (
                "/",
                "Packet Tracer herunterladen.url",
                b"[InternetShortcut]\r\nURL=https://www.netacad.com/resources/lab-downloads\r\n",
            ),
        ],
        intro="Die .pkt-Datei öffnen Sie mit Cisco Packet Tracer 8.2.",
    )

    s = c.section("Thema 4: IPv6 (nach den Weihnachtsferien)", available_from=NEVER)
    s.page(
        "ipv6",
        "IPv6 – Überblick",
        """
IPv6-Adressen sind 128 Bit lang und werden hexadezimal in acht Blöcken geschrieben, z. B.
2001:db8:40:20::1/64. Führende Nullen und eine Folge von Nullblöcken (::) dürfen
weggelassen werden. Subnetze im LAN sind immer /64.
""",
    )

    ds.probe(
        "Welches Netz hat VLAN 20 in Halle 4 und wie viele Hosts passen hinein?",
        "10.40.20.0/25, 126 Hosts",
        "Skript Subnetting S. 2, Subnetztabelle.xls, Netzplan",
    )
    ds.probe(
        "Auf welcher OSI-Schicht arbeitet ein Switch?",
        "Schicht 2, MAC-Adressen",
        "LF3_OSI_Modell.pptx",
    )
    ds.probe(
        "Was macht ARP?",
        "IP → MAC per Broadcast-Anfrage (nur per OCR lesbar)",
        "Scan_Lehrbuch_S110-112.pdf (Scan)",
    )
    ds.probe(
        "Was stand auf dem Tafelbild zur Blockgröße?",
        "256 − letztes Maskenoktett "
        "(nur per OCR; .jpg ist nicht in der Standard-Dateitypliste des Plugins)",
        "Tafelbild_2026-11-11.jpg",
    )
