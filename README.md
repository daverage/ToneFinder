# AI GP-50 Tone Builder

Local AI-assisted search for TONE3000 A2 NAM captures, followed by a
catalogue-validated GP-50 rig proposal and template-preserving `.prst` export.
See [`docs/VISION.md`](docs/VISION.md) for what this project is trying to do
and the principles behind how it's built.

## What it does

1. Sends your natural-language request to your local LLM (see below — LM
   Studio, llama.cpp, or an mlx-lm server this app can launch itself).
2. Converts the request into tone characteristics and three short catalogue searches.
3. Searches TONE3000 for NAM A2 tones.
4. Sends the candidate metadata back to the local LLM for reranking.
5. Shows the top matches with a reason and match score.
6. Lets you list and download A2 models while keeping the TONE3000 secret key on the server.
7. Builds a GP-50 supporting-effects rig around a selected NAM, using only exact
   effects and parameter definitions in `gp50_catalog.json`.
8. Lets you review or edit the JSON rig, then downloads a `.prst` built from a
   known-good blank GP-50 template.

## Setup

You need Python 3.10+ and an OpenAI-compatible local LLM server on port 1234
(LM Studio, llama.cpp's `llama-server`, or mlx-lm's `mlx_lm.server` — see
below, including how to have this app launch mlx_lm.server itself).

Install dependencies:

    python3 -m pip install -r requirements.txt

Generate a server-only TONE3000 **Secret Key** (it starts `t3k_cs_`) in your
TONE3000 settings. A publishable key (`t3k_pub_`) is an OAuth client ID and will
receive `401 Unauthorized` if used for these direct API calls.

### Config file (recommended over `export`)

Copy `.env.example` to `.env` and fill in what you need:

    cp .env.example .env
    # edit .env: set TONE3000_API_KEY=t3k_cs_your_key_here, and anything else below

`tone_finder.py` loads `.env` automatically on startup — every setting in this
README (`TONE3000_API_KEY`, `LMSTUDIO_BASE`, `MLX_MODEL`, `DDG_RESULTS`, etc.)
can go there instead of a long list of `export` commands. A real shell/OS
environment variable of the same name always overrides the file, so
`export FOO=bar` still works for a one-off change without editing `.env`.
`.env` is already in `.gitignore` since it typically holds your API key.

Then just run:

    python3 app.py

**Gotcha:** if you ever ran an `export FOO=bar` in a terminal (for example,
copying one of the `export` examples below) and later moved that same setting
into `.env`, the leftover export silently wins over the file — `.env` only
fills in variables that aren't already set, so an old export can look like
`.env` "isn't working." Check with `echo "$VAR_NAME"` in that terminal; if it
prints something, either `unset VAR_NAME` or just open a fresh terminal.

### Or set shell environment variables directly

`.env` is loaded by `tone_finder.py` itself, so it works the same way on
macOS/Linux and PowerShell — this is just the alternative for a one-off
override without a file.

macOS/Linux:

    export TONE3000_API_KEY="t3k_cs_your_key_here"
    python3 app.py

PowerShell:

    $env:TONE3000_API_KEY="t3k_cs_your_key_here"
    python3 app.py

Open:

    http://127.0.0.1:5000 (or HOST:PORT, if you set those in .env)

## Local LLM server (LM Studio, llama.cpp, or mlx-lm)

By default the app asks `http://127.0.0.1:1234/v1/models` and uses the first loaded model.

If you want to force a particular model:

    export LMSTUDIO_MODEL="your-loaded-model-id"

Or change the endpoint:

    export LMSTUDIO_BASE="http://127.0.0.1:1234/v1"

`LMSTUDIO_BASE`/`LMSTUDIO_MODEL` are not LM-Studio-specific: `lm_json`/`lm_model`
only require an OpenAI-compatible `/v1/chat/completions` and `/v1/models`, so
they work unchanged against `llama.cpp`'s `llama-server` or `mlx-lm`'s
`mlx_lm.server`, e.g.:

    llama-server -m your-model.gguf --port 1234
    # or
    mlx_lm.server --model your-mlx-model --port 1234

Nothing in this app is LM Studio-specific. Web research (below) is a direct
DuckDuckGo query, entirely separate from whichever server `LMSTUDIO_BASE`
points at — LM Studio, llama.cpp, or mlx-lm all work identically for both rig
building and research.

### Auto-launching mlx-lm (Apple Silicon)

On a Mac, the app can launch its own model server instead of requiring LM
Studio to already be running. Install `mlx-lm` (not in `requirements.txt`
since it is macOS/Apple-Silicon-only) and set `MLX_MODEL` to a local path or
Hugging Face repo id:

    python3 -m pip install mlx-lm
    export MLX_MODEL="mlx-community/Qwen3-8B-4bit"
    python3 app.py

On startup the app checks whether something is already answering at
`MLX_HOST`:`MLX_PORT` (default `127.0.0.1:8080`) and reuses it if so;
otherwise it runs `mlx_lm.server --model "$MLX_MODEL"`, waits for `/v1/models`
to respond (a first run also downloads the model's weights — raise
`MLX_STARTUP_TIMEOUT`, default 300 seconds, for a large model on a slow
connection), points `LMSTUDIO_BASE` at it, and stops the process on exit.
Setting `LMSTUDIO_BASE` yourself always takes precedence — the autostart is
skipped in that case. Leave `MLX_MODEL` unset to keep using LM Studio (or a
manually started llama.cpp/mlx-lm server) exactly as before.

`mlx-lm` requires a recent `transformers`. If this machine's Python already
has an older `transformers` pinned for another project (upgrading it in place
risks breaking that project), install `mlx-lm` into an isolated venv instead
of the shared environment, and point the app at that venv's binary with
`MLX_SERVER_BIN` rather than relying on `PATH`:

    python3 -m venv .venv-mlx
    ./.venv-mlx/bin/pip install mlx-lm
    export MLX_MODEL="mlx-community/Qwen3-8B-4bit"
    export MLX_SERVER_BIN="$(pwd)/.venv-mlx/bin/mlx_lm.server"
    python3 app.py

This is the recommended way to run it on a machine with other Python
projects — it's how the auto-launch path above was verified end to end.

The app does not disable model reasoning by default. If a specific local model
fails to produce final JSON because its thinking channel exhausts the response
budget, opt in to the compatibility workaround:

    export LMSTUDIO_DISABLE_THINKING=1

### Optional web research

The **Research this tone on the web** checkbox is enabled by default. It
queries DuckDuckGo directly via the [`ddgs`](https://pypi.org/project/ddgs/)
package (the maintained successor to the `duckduckgo-search` PyPI name) — no
API key, no local server, plugin, MCP, or tool call anywhere in the loop:

    python3 -m pip install ddgs   # already in requirements.txt

The returned snippets become research notes that are then handed to your
local LLM (whichever one `LMSTUDIO_BASE` points at) for interpretation via
`make_search_plan`/`lm_json` — so "web search" and "the local model" are two
separate, independently swappable steps: Python does the search, the local
model only ever sees already-fetched text.

`ddgs` is an unofficial HTML client, not an API with an SLA — it can break if
DuckDuckGo changes its markup, or get rate-limited under heavy use. If a
request fails, the app shows a note in the result and continues using the
local model's own knowledge rather than failing the search. Uncheck the box
to skip this step entirely.

## Notes

TONE3000 documents `/tones/search` as heavily rate-limited. This prototype deliberately limits each user request to three catalogue searches. For a production application, TONE3000 recommends contacting them about search/API usage.

The app requests `format=nam&architecture=2`, so search results are restricted to A2 NAM captures.

## GP-50 preset safety

The LLM makes musical choices only. `gp50/validator.py` checks every effect ID,
module, parameter name, range, step, toggle, block duplication, and chain length.
`gp50/preset.py` writes only documented records into a real template and then
recalculates CRC-8/0x07; it never synthesizes unknown GP-50 bytes.

The firmware 1.0.5 manual audit is recorded in
[`docs/GP50_MANUAL_V1_0_5_AUDIT.md`](docs/GP50_MANUAL_V1_0_5_AUDIT.md). The
manual verifies the effect inventory and UI-facing controls, while exact binary
parameter slots still require exported-preset or device-read verification.

The full reverse-engineered `.prst` binary format — header, CRC, name field,
every body record's byte layout, and what's still genuinely unconfirmed
(footswitch assignment, SnapTone's binary payload) — is documented in
[`docs/GP50_PRST_FORMAT.md`](docs/GP50_PRST_FORMAT.md), cross-checked against
an independent, hardware-verified reverse-engineering project
([drewmerc302/valeton-gp50](https://github.com/drewmerc302/valeton-gp50)).

Before preset export, place a 552-byte empty GP-50 preset exported from Valeton
Suite at `data/blank_gp50.prst`. This is intentionally required. SnapTone slot
binary encoding is not yet confirmed, so the selected NAM slot is displayed for
review but is not written into the N->S model record.

Run the small deterministic test suite with:

    python3 -m unittest discover -s tests -v
