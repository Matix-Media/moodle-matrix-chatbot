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
  blobs/aa/<sha256>  … and its files (fetch-legacy.sh copies both)
  backups/*.mbz      optional source: Moodle course backups, restored as they are
  files/…            anything a hand-written manifest points at with "path"
```

## Three ways to get a dataset

**From the legacy deployment.** Copies only the `documents` table and the file blobs
(no embeddings, chunks, PII tokens or credentials):

```bash
dev/seed/fetch-legacy.sh "ssh user@legacy-host" <api-container>
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
restrictions. Datasets with different prefixes can share one Moodle. To replace a
dataset that has the same prefix, run `down -v` first.

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
