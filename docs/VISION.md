# What this app is trying to do

This is a reference document, not a setup guide (see `README.md`) or an
agent-facing architecture map (see `CLAUDE.md`). It's the "why," for anyone
picking the project back up later and wanting the intent behind the
decisions, not just the code.

## The problem

Getting from "a tone in your head" to a working rig normally means already
knowing gear well enough to describe it in the first place, then either
trial-and-error on real hardware, or manually digging through hundreds of
unlabelled amp-capture files hoping to stumble on something close. There's
no way to search by *feel* — "warm blues breakup," "80s chorus clean,"
"Dave Grohl, Times Like These" — and get back something concrete you can
actually plug in and play, on either side of that search: a real captured
amp tone, or a real hardware multi-effects preset.

## The idea

Two independent halves, both driven by the same natural-language request:

1. **Find a tone.** Turn the request into search criteria (amp family,
   character, gain, optional web research for a named artist/song), search
   TONE3000 for matching NAM captures, and have the LLM rerank the results
   against the interpreted requirements — so the ranking reflects "does
   this actually fit the request," not just keyword overlap.
2. **Build a rig.** Independently, assemble a Valeton GP-50 preset —
   amp, cab, and supporting effects — chosen by an LLM reasoning about the
   same request, then validated and exported as a real, loadable `.prst`
   file.

Both halves end in something you can use immediately, not a description of
what you *should* go do.

## The principles behind how it's built

These came out of specific decisions made (and sometimes reversed) while
building this, not as an upfront design document:

- **The catalogue is the only authority, for hardware *and* musical
  knowledge.** `gp50_catalog.json` holds both the reverse-engineered
  hardware facts (fxids, parameter ranges, binary slots) and the musical
  semantics (what an effect sounds like, what it's for, its normalized
  tone profile). The LLM reasons about *musical* choices — "this needs a
  Tube Screamer-style push" — but can only ever select a real catalogue
  entry to express that choice. It never invents a model, an id, or a
  parameter, and Python never lets a fabricated one through.
- **Local-first, by design, not as an afterthought.** No cloud LLM
  dependency; the user's own local server (LM Studio, llama.cpp, or an
  mlx-lm instance this app can launch itself) does all the reasoning.
  TONE3000 access and its API key stay server-side. Web research is a
  direct, optional DuckDuckGo query, not a paid API or another vendor
  dependency.
- **The LLM proposes, Python disposes.** Every hardware-facing output
  (`gp50/validator.py`) is checked against the real catalogue before it's
  trusted — id, module, parameter name, range, step, chain length, block
  duplication — regardless of how the plan was produced (a clean LLM
  response, a partial one salvaged from a failed attempt, or a safe
  built-in fallback when the model fails outright). There is no path from
  "the LLM said so" to a binary file without going through this.
- **Never synthesize unknown bytes, and never guess when evidence is
  available instead.** `gp50/preset.py` edits a real, known-good exported
  preset in place and touches only documented records; everything else
  from the source export is preserved untouched. Where the binary format
  itself was uncertain (chain order, footswitch assignment), the answer
  came from actual evidence — byte-diffing real Suite exports, an
  independent hardware-verified reverse-engineering project
  (`drewmerc302/valeton-gp50`), and confirmation against a real device —
  not from a plausible-sounding guess. Two early guesses (the order record
  being a footswitch assignment; a swap-based fix for it) were wrong and
  were corrected once real evidence contradicted them; `docs/
  GP50_PRST_FORMAT.md` documents what's confirmed and what's still
  genuinely open, on purpose, rather than presenting a guess as settled.
- **Respect the constraints of a local model.** A prompt is only as good
  as what a modest local model can reliably follow: the catalogue sent to
  the LLM is deliberately compact (deduplicated descriptions, only
  relevant modules, capped list fields), and its own generation schema
  constrains the fxid choices to exactly what was shown — a local model is
  measurably more likely to hallucinate an id than a hosted frontier
  model, so the format is built assuming that failure mode will happen,
  not merely that it might.
- **Degrade gracefully, never fail silently or dangerously.** If the model
  can't produce a valid plan, `rig_builder` retries once, then salvages
  whatever repairable parts of the output it can, then falls back to a
  deterministic, catalogue-driven amp/cab choice — always ending in a
  usable, hardware-valid rig with a clear note about what happened, never
  a crash and never an unvalidated guess written to a file.

## What this deliberately isn't trying to do

- A general amp-modeling or IR research tool — it's specifically about
  getting from a request to a usable NAM capture or GP-50 preset, nothing
  broader.
- A SnapTone/NAM-refit generator — producing the compact on-device capture
  format from a NAM model is a real, separate reverse-engineering project
  (see `docs/GP50_PRST_FORMAT.md` §9); this app relies on Suite for that
  conversion and only ever *selects* an existing SnapTone, never invents
  the payload.
- A device-write tool — this app produces `.prst` files for Suite/manual
  transfer; it does not talk to the GP-50 over MIDI/SysEx itself.
- A multi-device product — it targets the GP-50 specifically. The format
  is shared with the GP-5 in places (per drewmerc302's project), but
  nothing here has been verified against a GP-5.

## Where the open edges are

Tracked honestly rather than papered over — see `docs/GP50_PRST_FORMAT.md`
§9 for the live list. As of this writing: why one known preset's chain-order
record isn't a valid permutation (a confirmed one-off, not blocking
anything), and the exact meaning of a couple of still-unwritten bytes. None
of these block current functionality; they're recorded so a future session
doesn't have to rediscover them from scratch, and so nobody mistakes an open
question for a confirmed fact.
