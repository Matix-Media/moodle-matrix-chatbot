# Seeding the dev Moodle with a dataset

The dev stack (`dev/docker-compose.yml`) seeds whatever dataset directory is mounted
at `/opt/dataset` — `dev/dataset/` unless `MLC_DATASET_DIR` says otherwise. Everything
becomes ordinary Moodle content, so the plugin syncs it to the API exactly as on a real
site: events → outbox → `/ingest`, the API downloading every file back from Moodle.

```
<dataset>/
  manifest.json      what to seed — the only file the seeder reads (format below)
  seed.toml          optional rules for build_manifest.py (dev/seed/seed.example.toml)
  documents.db       optional source: a legacy (Matrix-era bsbot) content index …
  blobs/aa/<sha256>  … and its files (both travel in a bundle, see below)
  backups/*.mbz      optional source: Moodle course backups, restored as they are
  files/…            anything a hand-written manifest points at with "path"
```

## Four ways to get a dataset

**Generate the synthetic demo dataset.** No real data, so it can be shared freely and
needs no bundle. `generate_demo.py` writes a whole fictional vocational-school class
(FIAE 25a, school year 2026/27, dates around November 2026–March 2027): six courses
built around one cross-subject project for an invented logistics company, so facts
connect across courses (the LF5 schema, LF8 API, LF3 network plan and the English
user guide all describe the same warehouse system).

```bash
python dev/seed/generate_demo.py dev/datasets/demo          # --force to regenerate
MLC_DATASET_DIR=./datasets/demo docker compose -f dev/docker-compose.yml up -d --wait
```

It covers every file type and link adapter the API handles: PDF (with text, and
image-only scans that need OCR), DOCX (and one named `.doc`), ODT, PPTX with speaker
notes, XLSX, legacy XLS, CSV, TXT (cp1252), Markdown, SQL, HTML/HTM, `.url` shortcuts,
PNG/JPEG/WEBP images (as files and embedded in pages), and live links of every adapter:
two public TaskCards boards, two HackMD notes, Google Docs, Slides and Sheets, two
Nextcloud shares (tab.digital) and three YouTube videos with transcripts. The TaskCards,
HackMD, Google and Nextcloud documents were made for this dataset and hold content that
belongs to it (group lists, class notes, a glossary, a pay calculator, …). Their text and
URLs are in `demo/external.py`; if one disappears, recreate it from there.
`python dev/seed/check_demo_links.py` fetches every link through the API's own adapters
and reports which still work (needs network). Some files are realistic noise: RTF, JSON and XML pass the plugin's file type filter but
have no extractor; SQL, images, `.url` and a Packet Tracer `.pkt` are outside the
plugin's default filter. Hidden modules, restricted sections and uneven enrolments
(student is not in LF8, student2 only in three courses) test access control.
`PROBES.md` in the dataset lists questions to ask, the expected answer and who may see
it. `tests/unit/test_dev_demo_dataset.py` keeps this coverage from going stale.

**From a legacy bundle.** The legacy deployment's content index (only the `documents`
table and the file blobs: no embeddings, chunks, PII tokens or credentials) was copied
once and is passed around as a bundle. Unpack it ([Bundles](#bundles-one-file-to-carry-a-dataset-between-checkouts))
and, to change the rules, rebuild:

```bash
python dev/seed/dataset_bundle.py unpack <bundle>.tar.gz dev/dataset
python dev/seed/build_manifest.py dev/dataset
```

**From Moodle course backups.** Back up courses on any Moodle (*Course → Backup*, or
`php admin/cli/backup.php --courseid=N --destination=DIR`), put the `.mbz` files into
`<dataset>/backups/` and build. Restores keep the course as it was: sections,
visibility, restrictions, groups, quiz questions, forum posts. User data inside the
backup is dropped unless `restore_users = true`; the manifest's users are enrolled
instead.

```bash
mkdir -p dev/dataset/backups && cp ~/Downloads/*.mbz dev/dataset/backups/
python dev/seed/build_manifest.py dev/dataset
```

Both sources can sit in one dataset. `build_manifest.py` turns `documents.db` into
generated courses and lists the backups after them.

**From anything else.** Write `manifest.json` yourself (or a converter that does),
check it, and skip `build_manifest.py`:

```bash
python dev/seed/build_manifest.py --check path/to/dataset
```

Then start (or `restart moodle` in) the stack. Further datasets go under
`dev/datasets/<name>/` (gitignored, like `dev/dataset/`). Docker only sees folders its
file sharing covers, and a folder it can't see mounts as empty and seeds nothing.

```bash
MLC_DATASET_DIR=./datasets/demo docker compose -f dev/docker-compose.yml up -d --wait
```

`MLC_DATASET_DIR` is relative to `dev/` and can also live in `dev/.env`. If the seeder
fails, it logs `dataset seed FAILED` (see `… logs moodle`) and Moodle starts anyway.

Seeding is idempotent and only adds. Each course and module is keyed by its
`idnumber`. A restart creates only what's missing and re-applies visibility and
restrictions. Sections have no idnumber, so on a restart each one is found through
the seeded modules it holds, not by its number. Sections you insert or move in the UI
keep their names. A dataset section you rename gets its manifest name back, and one
whose modules were all deleted is left alone. Datasets with different prefixes can
share one Moodle. To replace a
dataset that has the same prefix, run `down -v` first.

## Bundles: one file to carry a dataset between checkouts

Each worktree starts without `dev/dataset/`. A bundle packs a built dataset into one
`.tar.gz` that any checkout can seed from without the server it came from. It holds the
manifest, `seed.toml`, `documents.db` (so it can be rebuilt with other rules), the
backups and plain files, and only the blobs something refers to.

```bash
python dev/seed/dataset_bundle.py pack dev/dataset ~/.local/share/moodle-llm-chat/datasets/<name>.tar.gz
python dev/seed/dataset_bundle.py unpack ~/.local/share/moodle-llm-chat/datasets/<name>.tar.gz dev/dataset
docker compose -f dev/docker-compose.yml up -d --build --wait     # running: … restart moodle
```

Unpacking verifies every blob against its hash. It refuses members outside the target,
and it refuses to replace an existing dataset unless you pass `--force`. Keep bundles
outside the repository and outside synced folders such as iCloud `~/Documents`: they
hold real data.

## seed.toml

Rules for `build_manifest.py`, all optional. Unknown keys are an error. Every key is
documented, with its default, in [seed.example.toml](seed.example.toml):

| Key | Default | |
|---|---|---|
| `prefix` | `"legacy"` | starts every idnumber; keeps datasets apart |
| `category` | `"Legacy dataset"` / `"<prefix> dataset"` | course category |
| `courses` | all | legacy course ids to seed |
| `max_file_mb` | `0` (no limit) | larger files are skipped |
| `traps` | `"alternate"` | hidden module / restricted section per generated course |
| `restore_users` | `false` | keep user data inside backups |
| `[users.<name>]` | teacher, student, student2 | `role`, `pattern` ("1110" = in 3 of 4 courses), `firstname` |

## manifest.json (version 2)

```jsonc
{
  "version": 2,
  "prefix": "demo",                     // category idnumber: "<prefix>-dataset"
  "category": "Demo dataset",
  "users": [                            // created if missing, password Test1234!
    {"username": "teacher", "firstname": "Tina"}
  ],
  "courses": [{
    "idnumber": "demo:1",               // unique among courses and backups
    "fullname": "Netzwerke", "shortname": "demo-1",
    "enrol": {"teacher": "editingteacher"},   // username → role shortname
    "sections": [{                      // in order; the first is the general section
      "name": "Allgemeines",
      "available_from": 4070908800,     // optional: restricted until then, shown greyed
      "modules": [{
        "idnumber": "demo:1:plan",      // unique among modules
        "modname": "resource",          // page book folder resource url label
                                        // assign quiz forum choice feedback
        "name": "Stundenplan",
        "visible": false,               // optional, default true
        "intro": "<p>…</p>",            // optional description (labels: their text)
        "files": [{"filepath": "/", "filename": "plan.pdf", "path": "files/plan.pdf"}]
      }]
    }]
  }],
  "backups": [{"file": "backups/kurs.mbz", "idnumber": "demo:mbz:kurs", "enrol": {}}],
  "restore_users": false,
  "skipped": []                         // informational: what a converter left out
}
```

Per `modname`:

* `page`: `content` (HTML). Images in it are `files`, referenced as
  `@@PLUGINFILE@@/name`.
* `book`: `chapters` (required), a list of `{"title", "content"}`.
* `folder` / `resource`: `files` (required). Folders may use sub-`filepath`s such as
  `"/Woche 1/"`. A resource's first file is its main file.
* `url`: `externalurl` (required).
* Every type takes an optional `intro`.

A file is either `{"path": "<relative to the dataset>"}` or `{"blob": "<sha256>"}`,
which resolves to `blobs/<aa>/<sha256>`. Paths outside the dataset are refused.
