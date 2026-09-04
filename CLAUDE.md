# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local Flask app that: (1) turns a natural-language tone request into search
criteria via a local LLM, (2) searches TONE3000 for NAM A2 amp/cab captures
and AI-reranks them, and (3) builds a GP-50 supporting-effects rig around the
request and exports it as a template-preserving `.prst` file. See README.md
for full setup/config (local LLM server options, `.env`, mlx-lm autostart,
web research).

## Commands

Install deps: `python3 -m pip install -r requirements.txt`

Run the app: `python3 app.py` (serves `http://127.0.0.1:5000`; needs an
OpenAI-compatible LLM server reachable per `LMSTUDIO_BASE`, default
`127.0.0.1:1234/v1`)

Run all tests: `python3 -m unittest discover -s tests -v` (or `python3 -m pytest tests/ -q` — both work; no network or LLM server needed, the suite is fully deterministic)

Run a single test file/case: `python3 -m pytest tests/test_gp50.py -q` or `python3 -m unittest tests.test_rig_builder.RigBuilderTests.test_schema_limits_a_model_to_catalogue_fxids`

There is no lint/build/typecheck config in this repo — don't invent one.

## Architecture

### Two independent halves sharing one Flask app

- `tone_finder.py` — the LLM/TONE3000 half: `.env` loading, local-LLM HTTP
  calls (`lm_json`, `lm_model`, optional mlx-lm autostart), DuckDuckGo web
  research, TONE3000 search/rerank, and the `/api/search`, `/api/models/*`,
  `/api/download-model/*` routes. Defines the actual `app = Flask(__name__)`.
- `app.py` — the GP-50 half: imports `tone_finder.app` and adds
  `/api/build-rig` and `/api/create-preset`. This is the file you run
  (`python3 app.py`); it calls `tone_finder.autostart_llm_if_configured()`
  then `app.run(..., threaded=False)`.
- `gp50/` — a self-contained package with **no Flask/LLM dependency**:
  catalogue access, rig validation, and binary preset serialization. Treat it
  as the trusted core; `tone_finder.py`/`app.py` are the untrusted-input
  boundary around it.

### Concurrency: exactly one LLM call in flight, ever

A local LLM's memory (unified memory on Apple Silicon) isn't cleanly
OOM-killed if overcommitted — it can hard-freeze the machine. This is
enforced two ways simultaneously, deliberately redundant: `app.run(...,
threaded=False)` (one request at a time, full stop) and every LLM-backed
route wraps its whole body in `tone_finder.LLM_BUSY_LOCK`. Do not add
threading/multiprocessing/async concurrency around LLM calls, and don't
remove either guard even though the other would still work.

### `gp50/` — hardware AND musical knowledge authority

`gp50_catalog.json` (repo root, generator in `gp50_catalog_builder/` — that
subpackage's own `gp50_catalog.py`/README describe an older standalone
design and are **not** what the app actually uses; the live implementation is
`gp50/catalog.py`) is the single source of truth for both hardware facts and
musical/semantic knowledge:

- Hardware: `fxid`, `fxid_hex`, `module`, `module_id`, `name`, `title`,
  `type`, `origin`, and each `params[]` entry's `alg_id`/`min`/`max`/`step`/
  `default`/`toggle`/`unit`. Reverse-engineered and treated as immutable
  ground truth — never invent or "correct" these without explicit evidence.
- Musical knowledge lives alongside it: a top-level `effect_types` (generic
  per-type description) plus, per catalogue entry, an optional
  `musical_profile` (specific model character/best_for/roles/keywords),
  `tone` (a normalized 0.0–1.0 vector, dimensions vary by module — drive/amp
  gear gets gain/compression/brightness/etc., delays get brightness/warmth/
  clarity/modulation, reverbs get brightness/warmth/size/density, cabs get
  low_end/mid_focus/brightness/tightness/aggression, and CAB additionally
  gets `pairs_well_with` amp-family affinity keywords), `knowledge`
  (`origin_confidence`/`profile_confidence`), and per-parameter `semantic`
  hints for non-obvious controls (e.g. a RAT-style `Filter` knob that darkens
  as you turn it up). SnapTone/User IR slots deliberately have none of this —
  they're either empty or user-supplied, so there's nothing to describe.
- `gp50/catalog.py`'s `default_catalog()` is a process-wide cached instance —
  `build_rig`/`validate_rig`/`create_preset` all default to it instead of
  constructing their own `GP50Catalog()`. The catalogue is immutable read-only
  data, so re-parsing the ~400KB JSON and rebuilding its fxid index on every
  request was pure per-request churn; use `default_catalog()` for any new
  internal call site, and only construct `GP50Catalog(path)` directly when a
  specific file actually matters (tests pin an explicit path for isolation).
- `gp50/catalog.py`'s `GP50Catalog` loads and interprets this JSON — it does
  not hardcode musical knowledge in Python. `musical_profile(effect)` merges
  the generic type profile with the entry's specific one (list fields like
  `keywords`/`character` are merged, not replaced, so a specific model stays
  matchable on its type's generic terms too). `score_effect_relevance` is a
  deterministic, hand-weighted matcher (exact name/origin > type > roles >
  keyword phrases > character > best_for > generic words), with an optional
  `target_tone` vector for ranking by sonic similarity when a request is
  pure descriptive words with no literal keyword overlap.
- `compact_for_prompt()` is what actually gets sent to the LLM — deliberately
  leaner than the full catalogue data: no binary metadata, a deduplicated
  `legend` keyed by type (or `type:name` when a model has its own specific
  profile) instead of repeating each description per entry, and
  `GP50Catalog._prompt_profile()` further trims each legend entry (drops
  `keywords`/`watch_out`, caps list fields) so prompt size doesn't balloon —
  this directly affects local-LLM memory/context usage, so don't casually
  add fields back into the prompt path without checking the size impact
  (measure with `len(json.dumps(catalog.compact_for_prompt(...)))`).

### Rig-building pipeline (`gp50/rig_builder.py` → `validator.py` → `preset.py`)

`build_rig()` is the LLM-facing entry point: narrows the catalogue to
relevant modules (`_relevant_modules`, keyword-matched from intent text),
calls the LLM with a schema whose `fxid` enum is restricted to exactly the
catalogue entries shown in the prompt (so the model can't reference an
unlisted id even if it hallucinates one), retries once on validation failure,
and falls back through `_salvage_valid_blocks` → `_builtin_fallback` (a safe
amp/cab-only rig picked by `score_effect_relevance`/tone similarity) if the
model still can't produce a valid plan. `_finalize_rig` then applies, in
order: sourced reference settings (`_apply_reference_settings`, only onto a
block whose catalogue name/origin matches the named real device), gain
staging (`_manage_gain`), and effect-sympathy conservative defaults
(`_review_effect_sympathy`), tracking a `locked` set of (module, param) pairs
so those later safety passes never overwrite an explicitly sourced value.
`_finalize_rig` finally sorts `signal_chain` into real hardware order
(`MODULE_ORDER`, matching NR/PRE/DST/AMP/CAB/EQ/MOD/DLY/RVB) before
returning it — this is display-only bookkeeping (the model may emit blocks
in any order, e.g. AMP before a PRE compressor) and has no effect on the
binary preset, since `create_preset` writes each block by its own fixed
`module_id` offset regardless of list position. `_relevant_modules` always
offers RVB alongside AMP/CAB (not gated on the interpreted intent naming
reverb explicitly) so a request that never says "reverb" isn't structurally
prevented from getting a schema-valid touch of one.

The GP-50's `order` binary record is the **DSP signal-chain order**
(`order[chain_position] = module_id`), not footswitch assignment — an
earlier version of this codebase guessed footswitch assignment from a
single byte-diff and was wrong. Confirmed by an independent, hardware-
verified source (`drewmerc302/valeton-gp50`'s `re/DEVICE_BLOCKORDER.md`: a
live-hardware drag-reorder changed only these 10 bytes) and cross-checked
against a second real Suite export (`data/Mick Ronson Lead (1).prst`) —
see `docs/GP50_PRST_FORMAT.md` for the full write-up and open questions
(that real export's `order` isn't a strict permutation, unlike every
hardware-read example in that project, which is unresolved). `preset.py`'s
`create_preset()` now leaves this record untouched rather than rewriting
it: the blank template's default order already places movable blocks
(NR/PRE/MOD/DLY/RVB) in the conventional pedalboard arrangement around the
fixed DST/N->S/AMP/CAB/EQ core, and there's no per-request signal to justify
writing a different one.

Real footswitch binding is a separate record — magic `03 00 0A 00`,
`[FS1 u32][FS2 u32]` block-index bitmasks plus 2 trailing LED-state bytes
(`byte = 5 + (1 if a block bound to that footswitch is on)`) — confirmed
against 7 real exports across 6 distinct presets (`data/*.prst`), including
a hardware pair where FS2 was physically pressed on the real unit.
`preset.py`'s `_assign_footswitches()` writes the full record: FS1→PRE,
FS2→DST whenever that block type is present in the generated rig, and the
derived LED bytes from the final masks. See `docs/GP50_PRST_FORMAT.md` §7
for the full write-up; §9 covers what's still open — of those same 7
exports, exactly one preset (used 3 times) has a non-permutation `order`
record while every other one (factory or user-built) matches blank's
default exactly, so it looks like a one-off tied to that specific preset,
not a general rule.

`gp50/validator.py`'s `validate_rig()` is the hard boundary between "LLM
output" and "trusted rig": every fxid/module/parameter name/range/step/
toggle is checked, and — importantly — **any parameter the plan didn't set is
filled in from the catalogue's own documented `default`**, never left
missing. This matters because `gp50/preset.py`'s `create_preset()` only
writes a byte for a parameter that's present in the block's `parameters`
dict; anything absent would otherwise silently keep whatever value the blank
template happens to have at that offset, unrelated to the effect actually
selected. Every rig, however it was produced (LLM, salvage, builtin
fallback), must go through `validate_rig` for this reason alone.

`gp50/preset.py` edits a real 552-byte blank GP-50 export
(`data/blank_gp50.prst`, must be supplied — never synthesized) by locating
documented records via fixed byte markers (`RECORDS`) and writing only
known fields, then recalculates the CRC-8/0x07 checksum. SnapTone binary
encoding is unconfirmed, so a selected SnapTone slot is shown for review but
not written into the `N->S` model record.

### Frontend

`templates/index.html` + `static/js/app.js` (vanilla JS, no build step) talk
to the Flask routes above. `static/js/app.js` has two workspaces (tone
search vs. GP-50 rig builder) sharing one intent/interpretation panel.
