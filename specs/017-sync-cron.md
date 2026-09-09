# 017 — Scheduled sync cycle

- **Status:** active
- **Tests:** `tests/unit/test_sync_loop.py`

## Goal

`bsbot cron` is the unattended, long-running loop meant to run in its own container: repeatedly
sync the Moodle manifest, index whatever is newly pending, and embed whatever chunks lack a
vector, on an interval, so the corpus stays fresh with nobody running `sync`/`index`/`embed` by
hand. It must run as its own process, separate from `bsbot serve` (the Matrix bot) and `bsbot
serve-api` (the web API) — a multi-hour crawl or a large embedding batch must never delay
either of those from answering. Deferred earlier in favour of retrieval-quality work (specs
005–007), it exists now that the corpus needs to stay current without a human watching a
terminal.

## Acceptance criteria

### The cycle
- `AC-1` One cycle runs sync, then index, then embed, in that order — each phase's output is
  what the next phase reads from disk.
- `AC-2` `sync` re-crawls every enrolled course and persists the manifest. Structure only, no
  file downloads — cheap enough to run every cycle regardless of interval.
- `AC-3` `index` only touches documents the manifest reports as pending; a cycle that finds
  nothing new performs no extraction and triggers no embedding calls.
- `AC-4` `embed` selects chunks lacking a vector directly from the store, rather than re-deriving
  "pending" from the manifest a second way.

### Failure isolation
- `AC-5` Each of the three steps is isolated: a step raising an exception is caught, logged by
  name (`cron.step_failed`, carrying the error) and every remaining step still runs — a Moodle
  outage during sync must not also cancel an otherwise-healthy embed pass over content already
  indexed from a previous cycle.
- `AC-6` A cycle in which no step fails logs no `cron.step_failed` event.
- `AC-7` An exception raised by the cycle *itself* (as opposed to one of its three named steps)
  propagates out of the loop rather than being swallowed — isolation is a property of the three
  steps, not blanket protection around anything called "a cycle."

### The interval loop
- `AC-8` Between cycles, the loop sleeps for the configured interval in minutes; it does not
  sleep after the final cycle when a cycle limit is set (a test-only escape hatch — production
  always runs unbounded).
- `AC-9` `bsbot cron` runs as its own process (its own container in docker-compose), independent
  of `bsbot serve` and `bsbot serve-api`.

## Non-goals

- No per-course or per-document scheduling granularity — one interval governs the whole cycle.
- No distributed or multi-worker coordination; a single running instance of the loop is assumed.
- No change to what `sync`/`index`/`embed` themselves do — this spec covers only the loop and
  the failure isolation around it. The underlying behaviour is specs 002/003 (sync), 005/006/015
  (index), and 007 (embed).

## Notes

`sync_once`/`index_once`/`embed_once` (`src/bsbot/sync_loop.py`) are thin wrappers around the
same classes the one-shot `bsbot sync`/`index`/`embed` CLI commands use — nothing about *what*
each phase does is new here, only the repetition and the isolation between phases.
