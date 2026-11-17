"""English: technical English for application developers (unit 3: customer support)."""

from __future__ import annotations

from .. import images, render
from ..external import URLS
from ..model import Dataset
from ..school import CLASS, DATES, HEADER, PROJECT, teacher

AUTHOR = teacher("WHI")

UNIT3 = [
    (
        "3.1 Taking a support call",
        """
When a user calls the help desk, follow the **LAST** pattern: Listen, Ask, Solve, Thank.

| Phase | Useful phrases |
|---|---|
| Greeting | Nordlicht IT support, this is Emre speaking. How can I help you? |
| Asking | Could you tell me what exactly happens when you scan the label? |
| Clarifying | Just to make sure I understand: the scanner beeps but the stock doesn't change? |
| Solving | Could you please restart the app and try again while I stay on the line? |
| Closing | Is there anything else I can do for you? Thanks for calling. |

> Avoid "You must…" – it sounds rude in English. Prefer "Could you…" or "Would you mind…".
""",
    ),
    (
        "3.2 Describing problems",
        """
Use the **present continuous** for what is happening now ("The scanner is showing an error")
and the **present perfect** for results ("I have restarted the device twice").

Typical vocabulary: to freeze, to crash, to time out, error message, to be stuck, to
reboot, to reinstall, a workaround, to escalate a ticket.

Exercise: Write a ticket for this situation – the handheld scanner in hall 2 shows
"HTTP 401 Unauthorized" since 6 a.m.; restarting did not help; 3 colleagues are affected.
""",
    ),
    (
        "3.3 Writing a short user guide",
        f"""
For the English part of the {PROJECT} project you write a quick reference guide for
warehouse staff (max. two pages). Rules for good instructions:

1. Use the imperative: "Scan the shelf label first."
2. One action per step, numbered.
3. Put warnings **before** the step they refer to.
4. Use the same word for the same thing (always "shelf", never "rack" and "shelf").
5. Add a short troubleshooting table at the end.
""",
    ),
    (
        "3.4 The project abstract",
        """
An abstract summarises a project in 150–200 words: **context, goal, method, result.**

Example opening: "Nordlicht Logistik GmbH manages the stock of three warehouses in an
Excel workbook. This project developed a prototype of a web-based warehouse management
system with a relational database and a REST interface for handheld scanners."

Write in the past tense for what you did and in the present tense for what the system does.
""",
    ),
]

VOCAB = """
| English | German | Example |
|---|---|---|
| warehouse | Lager(halle) | The new warehouse opens in March. |
| stock level | Lagerbestand | The stock level is below the minimum. |
| reorder point | Meldebestand / Mindestbestand | Reorder when stock reaches the reorder point. |
| handheld scanner | Handscanner | Each picker uses a handheld scanner. |
| to pick an order | einen Auftrag kommissionieren | We pick about 250 orders a day. |
| shipment | Sendung | The shipment left on Tuesday. |
| to troubleshoot | Fehler suchen und beheben | Let's troubleshoot the connection. |
| to escalate | (ein Ticket) weiterleiten | I'll escalate this to second-level support. |
| outage | Ausfall | There was a network outage in hall 2. |
| requirement | Anforderung | This is a must-have requirement. |
| deadline | Abgabetermin | The deadline is 22 January. |
| to deploy | bereitstellen, ausrollen | We deploy the API on Friday. |
| subnet | Teilnetz | Each VLAN has its own subnet. |
| foreign key | Fremdschlüssel | order.customer_id is a foreign key. |
| to log in / login | sich anmelden / Anmeldung | Log in with your staff number. |
"""

CLOUD_READING = """
# Reading: Cloud or on-premises?

Many small and medium-sized companies face the same question when they replace old
software: should the new system run **in the cloud** or **on-premises**, on servers in
the company's own data centre?

Cloud services are paid monthly and scale easily. Updates and backups are done by the
provider, so the company needs fewer IT staff. On the other hand, the company depends on
a stable internet connection, and data protection rules (GDPR) require a careful look at
where the data is stored.

On-premises systems require an investment in hardware and staff, but the company keeps
full control. In logistics, where handheld scanners must keep working even if the
internet connection fails, many firms choose a hybrid approach: the warehouse system runs
locally, while e-mail and office software are cloud-based.

## Tasks

1. Find three advantages of each option in the text.
2. Which option would you recommend for Nordlicht's warehouse system? Give two reasons.
3. Explain the word "hybrid" in your own words.
"""


def build(ds: Dataset) -> None:
    c = ds.course(
        "englisch",
        f"Englisch – Technical English ({CLASS})",
        {"teacher": "editingteacher", "student": "student"},
    )

    s = c.section("General")
    s.page(
        "welcome",
        "Welcome to Technical English",
        f"""
Hi everyone! In this course we practise the English you need at work as an application
developer: support calls, documentation, e-mails and presentations. Lessons are on
**Wednesdays, periods 1–2, room 1.07**.

## Assessment

- Vocabulary test Unit 3: **{DATES["en_vocab"]}** (20 minutes)
- English user guide and abstract in the project (counts as one test)
- Class participation

Office hour: Wednesday after period 2, or e-mail me any time. – {AUTHOR}
""",
    )
    s.url("hackmd_phrases", "Phrasebank (HackMD)", URLS["hackmd_phrasebank"])

    s = c.section("Unit 3: Customer support")
    s.book("unit3", "Unit 3 – Customer support", UNIT3, intro="Work through one chapter per week.")
    s.resource(
        "vocab",
        "Vocabulary list Unit 3",
        "Vocabulary_Unit3.docx",
        render.to_docx("Vocabulary Unit 3 – Logistics & Support", VOCAB, author=AUTHOR),
        intro=f"Learn these words for the vocabulary test on {DATES['en_vocab']}.",
    )
    s.resource(
        "phone",
        "Support call script (HTML)",
        "support_call_script.htm",
        render.to_html_document(
            "Support call role play",
            """
## Role card A – caller

You work in hall 2. Since this morning your scanner shows "Item not found" for every
article starting with NL-0. Describe the problem, answer questions politely.

## Role card B – help desk

Follow the LAST pattern. Find out: which device, since when, which error message, what has
already been tried. Offer a workaround and open a ticket.
""",
            lang="en",
        ),
    )
    s.url(
        "gdoc_roleplay",
        "Shared role-play notes (Google Docs)",
        URLS["gdoc_roleplay"],
    )
    s.url(
        "yt_osi_en",
        "Video: OSI model animation (English)",
        URLS["youtube_en"],
        intro="Listening practice that connects with LF3. Note five new technical words.",
    )
    s.page(
        "settings",
        "Reading a settings screen",
        """
Look at the screenshot of the scanner app settings and answer: Which server does the app
connect to? How long does it wait before a timeout? What would you change if the scanners
in hall 4 cannot connect?
""",
        images={
            "scanner_settings.webp": images.screenshot(
                "NLV Scanner App - Settings",
                [
                    ("Server URL", "https://nlv.nordlicht.example/api/v1"),
                    ("Device ID", "SCN-H2-017"),
                    ("Timeout (seconds)", "8"),
                    ("Language", "English (UK)"),
                    ("Sound on success", "on"),
                    ("Offline queue", "enabled, max. 200 bookings"),
                ],
            )
        },
        raw_html='<p><img src="@@PLUGINFILE@@/scanner_settings.webp" alt="Settings screen of '
        'the scanner app" width="700"></p>',
    )

    s = c.section("Reading and writing")
    s.resource(
        "cloud",
        "Reading: Cloud or on-premises?",
        "Reading_Cloud_vs_OnPrem.pdf",
        render.to_pdf(
            "Reading: Cloud or on-premises?",
            CLOUD_READING,
            header=f"{HEADER} · English",
            author=AUTHOR,
        ),
    )
    s.resource(
        "guide",
        "Writing guide (Markdown)",
        "writing_guide.md",
        render.to_markdown(
            "Writing guide: e-mails and documentation",
            """
## Formal e-mails

- Subject line: short and specific ("Scanner error in hall 2 – ticket #4471")
- Greeting: "Dear Mr Lüdemann," / "Hello Sven," (if you are on first-name terms)
- First sentence: why you are writing
- Close: "Kind regards" or "Best regards" + full name

## Documentation

- Write for the reader, not for yourself
- Short sentences, active voice
- Use lists and tables for steps and settings
""",
        ),
    )
    s.activity(
        "assign",
        "abstract",
        "Draft: project abstract",
        f"""
Upload the first draft of your project abstract (150–200 words) by 13 January 2027. I will
give feedback before the final hand-in on {DATES["projektabgabe"]}.
""",
    )

    ds.probe("What does LAST stand for?", "Listen, Ask, Solve, Thank", "Book Unit 3, chapter 3.1")
    ds.probe(
        "What is the English word for Mindestbestand?", "reorder point", "Vocabulary_Unit3.docx"
    )
    ds.probe(
        "Wie lange ist das Timeout in der Scanner-App?",
        "8 Sekunden (Bild auf der Seite, nur über OCR/Alt-Text teilweise sichtbar)",
        "Seite 'Reading a settings screen'",
    )
    ds.probe(
        "Which phrase can I use to apologise for a delay?",
        '"I do apologise for the delay – we are working on it."',
        "Phrasebank (HackMD, extern)",
        "student, teacher",
    )
    ds.probe(
        "What was the problem in role play pair 2 and how was it solved?",
        "label printer in hall 1 printed empty labels; thermal paper was inserted the wrong way",
        "Shared role-play notes (Google Docs, extern)",
        "student, teacher",
    )
