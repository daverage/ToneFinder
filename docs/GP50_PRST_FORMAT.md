# Valeton GP-50 `.prst` binary format — reverse-engineered

There is no vendor spec for this format. This document combines what this
project independently confirmed (from `gp50_catalog.json`'s own reverse
engineering, this codebase's `gp50/preset.py`, and two real Valeton Suite
exports byte-diffed against `data/blank_gp50.prst`) with an independent,
hardware-verified reverse-engineering project found while researching this,
credited throughout. Every claim below is either (a) independently confirmed
by both sources, (b) confirmed only by this project's own byte-diffs, or
(c) confirmed only by the external project's live-hardware work and not
re-verified here — each is labeled. Section 8 lists what is still genuinely
open.

**External source**: [drewmerc302/valeton-gp50](https://github.com/drewmerc302/valeton-gp50)
— a browser-based GP-50/GP-5 editor built by reverse-engineering the SysEx
protocol from scratch (Ghidra decompilation of the vendor's own macOS driver,
MIDI Monitor packet capture, and — critically for the `.prst` file itself —
live-hardware read/write round-trips over WebMIDI, not just static file
diffing). Relevant files: `patch/prst_format.py` (their parser, run directly
against this project's sample files to cross-check the notes below),
`re/DEVICE_READ.md`, `re/DEVICE_BLOCKORDER.md`, `re/SNAPTONE_PROTOCOL.md`.

## 1. File layout

A `.prst` is exactly **552 bytes** for the GP-50 (drewmerc302's project also
handles a 507-byte GP-5 variant sharing the same container — not used here).

| Range | Contents | Confirmed by |
|---|---|---|
| `0x00:0x14` | Constant 20-byte header, `"GP-50\0\0\0\0\0\0\0\0\0\0\0\0\0\x01\0\0"` | Both |
| `0x14` | File CRC — CRC-8/0x07 over `prst[0x15:]` | Both |
| `0x15:0x19` | `FF FF FF FF` sentinel | External |
| `0x19:0x29` | 16-byte patch name, Latin-1, NUL-terminated/padded | Both |
| `0x29:0x244` (552 bytes total) | 511-byte body, containing the TLV records below | Both |

**CRC**: CRC-8, polynomial `0x07` (CRC-8/SMBUS), init `0`, no reflection, no
final XOR — the same algorithm this project's `gp50/preset.py:crc8_07` and
`gp50_catalog_builder`'s prior work already used, now independently confirmed
by drewmerc302's Ghidra decompilation of the vendor driver (the 256-entry
table lives in `5868USB.dylib` at a fixed offset, shared with that binary's
bundled FLAC decoder). It is computed over `prst[0x15:]` — from the sentinel
through the end of the file, i.e. name + body, not including the header.

**Name field**: 16 bytes, up to 15 usable characters + a NUL terminator —
and the GP-50 hardware screen correctly displays a name using the full 15.
But **Valeton Suite's own preset browser has a shorter real limit: 10
characters, not 15.** Resolved 2026-09 from a live round trip: this
project generated a preset named "The lead guitar" (15 characters); it
showed correctly on the device but blank in Suite's preset list. Importing
that file into Suite and re-exporting it *from Suite itself*, unedited,
produced a file with the name silently truncated to "The lead g" (10
characters) — Suite's own save path drops anything past 10. Every other
real Suite-authored name this project has on file independently tops out
at 10 characters too (`Pure Clean`, `Mick Ronso` — itself very likely a
Suite-side truncation of a longer intended "Mick Ronson"). One earlier real
export (`data/Mick Ronson Lead (1).prst`, 16 characters, no terminator) is
consistent with this same limit rather than a counterexample to it — it's
simply another name too long for Suite to render, not evidence a full 16
bytes works. `gp50/validator.py` and `gp50/rig_builder.py` enforce the
10-character limit upstream of this module for exactly this reason;
`preset.py`'s own 15-usable-byte ceiling is only a defensive fallback
against the binary field itself.

## 2. Body records (TLV, found by magic bytes — not fixed offsets)

The body is a sequence of tag-length-value records. **Always locate a record
by searching for its magic bytes**, never by a hardcoded offset — the actual
starting offset has been observed to shift between exports (both projects
agree on this).

| Magic (hex) | Payload size | Contents |
|---|---:|---|
| `03 30 28 00` | 40 bytes | **Models**: 10 × 4-byte model records |
| `01 30 04 00` | 4 bytes | **Bypass**: `u32` bitmask, bit `k` = block `k` active |
| `02 30 0A 00` | 10 bytes | **Order**: DSP chain order (see §5 — not footswitches) |
| `04 30 40 01` | 320 bytes | **Params**: 80 × `float32`, little-endian |
| `03 00 0A 00` | 10 bytes | **Footswitches**: `[FS1 u32][FS2 u32]` block-index bitmasks + 2 unknown bytes (§7) |

Both projects agree on all five magics and sizes, and on the semantics of
models/bypass/params/footswitches (§7 covers how the footswitch trailer
went from "hypothesized" to independently confirmed). This project's
`gp50/preset.py:RECORDS` and drewmerc302's `patch/
prst_format.py:REC_MODELS/REC_BYPASS/REC_ORDER/REC_PARAMS`/`FS_TRAILER` are
byte-identical definitions, arrived at independently. The GP-5 variant uses
`03 00 08 00` for the footswitch trailer instead (an 8-byte payload — not
used by this project, which only targets the GP-50).

## 3. Block index / module_id table

Both the models and params records are indexed by a fixed **block index**
(0–9), one per module type, independent of where that module type appears in
a signal chain. This project calls this `module_id` (`GP50Catalog.module_id`,
sourced from `gp50_catalog.json`); drewmerc302's project calls it "block
index" / classifies it by a **category byte** inside each model record. Both
schemes agree exactly:

| Index | Module | drewmerc302's category byte |
|---:|---|---|
| 0 | NR | `0x00` (shared with PRE) |
| 1 | PRE | `0x00` (shared with NR) |
| 2 | DST | `0x03` |
| 3 | AMP | `0x07` |
| 4 | CAB | `0x0a` |
| 5 | EQ | `0x01` |
| 6 | MOD | `0x04` |
| 7 | DLY | `0x0b` |
| 8 | RVB | `0x0c` |
| 9 | N->S (SnapTone) | `0x0f` |

## 4. Models record — 10 × 4-byte model records

Each 4-byte record is a little-endian `fxid`: this project reads it directly
as `struct.unpack("<I", ...)`; drewmerc302's project decomposes the same four
bytes as `fxid = (category_byte << 24) | fxlow_24bit`, where `fxlow` is the
model index within that category and the category byte is the high byte —
algebraically the identical little-endian `u32`, just named differently.
Record `k` (by position in this 40-byte block) always holds the model
currently assigned to block index `k` (§3) — this **does not change** when
the signal-chain order changes (§5); only the order record does.

Confirmed on both `data/blank_gp50.prst` and `data/Mick Ronson Lead (1).prst`
against `gp50_catalog.json`'s own `fxid` values — every decoded fxid in both
files matches a real catalogue entry for the expected module.

## 5. Order record — DSP chain order, NOT footswitch assignment

**This project initially misread this record.** An earlier version of
`gp50/preset.py` inferred, from a single byte-diff against one real preset,
that `order[0:2]` encoded FS1/FS2 footswitch assignment, and wrote a
swap-based heuristic to set it. That was wrong, and the swap logic could
corrupt the record in cases where the intended target's default position
fell later in the array.

**Confirmed correct interpretation** (drewmerc302's
`re/DEVICE_BLOCKORDER.md`, from a live-hardware experiment — not just static
file diffing): `order[chain_position] = block_index`. It is the actual
**DSP signal-chain order** — which block processes the signal in which
position — set by dragging a block's position in the UI. Their method: read
a patch body over WebMIDI as a baseline, drag one block to a new chain
position on the real device, save, re-read, diff. Result: dragging RVB
(block 8) from last to first changed **only** the order bytes (plus the
CRC) — nothing else:

```
before (RVB last):  [0, 1, 2, 9, 3, 4, 5, 6, 7, 8]
after  (RVB first): [8, 0, 1, 2, 9, 3, 4, 5, 6, 7]
```

Reconstructing the "before" order onto the "after" file's other bytes and
refixing the CRC reproduced the original file byte-for-byte, confirming the
order array (plus the CRC) is the *only* thing a chain reorder touches.

**The movable/core split** (same source): the model records fall into two
classes by category byte —

- **Core** (immutable relative order, always contiguous): DST, N->S, AMP,
  CAB, EQ — categories `0x03, 0x0f, 0x07, 0x0a, 0x01`.
- **Movable** (can sit anywhere before/after the core, in any order): NR,
  PRE, MOD, DLY, RVB — categories `0x00 (NR/PRE), 0x04, 0x0b, 0x0c`.

The blank template's default order, `[0, 1, 2, 9, 3, 4, 5, 6, 7, 8]`, places
this exactly the way a conventional pedalboard would: movables NR, PRE
*before* the core, movables MOD, DLY, RVB *after* it. This matches the
device's documented standard signal-chain order exactly: Noise Reduction,
PRE, Distortion, SnapTone, Amp, Cab, EQ, Mod, Delay, Reverb (an expression
pedal also exists on the physical unit but isn't a chain block/record).

**This project's policy, given the above**: `gp50/preset.py:create_preset()`
no longer writes to the order record at all — it passes the template's
order straight through. The default is already a correct, conventional
signal chain for every rig this app builds (gate/comp/drive before the amp,
modulation/delay/reverb after), and there is no per-request signal (nothing
in the LLM's rig-building output describes a desired chain reorder) to
justify computing a different one. This sidesteps the open discrepancy in
§9 entirely rather than resolving it by guessing.

## 6. Bypass record

`u32` bitmask, bit `k` = block index `k` (§3) is active/enabled. Both
projects agree. Confirmed against `data/Mick Ronson Lead (1).prst`: mask
`0b10000110` = bits 1, 2, 7 set = PRE, DST, DLY — matching that preset's
actual audible effects (a wah, a fuzz, and a delay). AMP/CAB/N->S bits are
apparently never set in real exports (there is no "bypass the amp" concept)
— this project's own preset generation sets them anyway when marking those
blocks `enabled: true`, which appears to be harmless (the firmware likely
ignores those bits) but is not independently confirmed to be correct.

**Confirmed live**: `data/66-Mick Ronso (DST on/off).prst` is the same
preset exported twice, with DST toggled between the two exports by
physically pressing footswitch 2 on the real unit (user-confirmed — not
inferred from the file). The only bits that changed are the DST bit in
this bypass mask (plus one byte in the footswitch trailer below, which
turned out to be part of that same record — see §7) — i.e. this
independently confirms a footswitch press at runtime does exactly what §6
already describes: flips one block's bypass bit, nothing else in the saved
chain/model state.

## 7. Footswitch trailer record — CONFIRMED

drewmerc302's project hypothesizes a trailer record, magic `03 00 0A 00`,
holding `[FS1 u32][FS2 u32]` plus 2 trailing bytes, implemented in their
`app/patchlib.py:_footswitches()` (each mask's bit `k` = block index `k`,
§3 — the same footswitch toggles every set bit it has; a mask can have
more than one bit — confirmed in `data/01-Neo Soul (factory).prst`, whose
FS2 is bound to both DLY and RVB at once).

This project's earlier draft of this document listed this as unconfirmed,
because reading it from `data/blank_gp50.prst` and `data/Mick Ronson Lead
(1).prst` returned `0` for both masks in both files — which turned out to
simply be correct (neither preset has a footswitch assigned), not a sign
the location was wrong. **Confirmed** against `data/66-Mick Ronso (DST
on/off).prst` — a real-hardware pair where the user physically pressed
footswitch 2 to toggle DST:

```
offset 542 (+4 magic): fs1 = 0x00000002  (bit 1 = PRE)
offset 546:            fs2 = 0x00000004  (bit 2 = DST)
offset 550, 551:       trailing bytes — see below
```

`fs2`'s bit exactly matches DST, the block the user reports FS2 actually
controls, and — critically — **both masks are byte-identical whether
DST's own bypass bit is on or off**. So the footswitch-to-block assignment
is confirmed independent of the block's current on/off state, exactly
like a real pedalboard footswitch binding should behave (the assignment
persists; only the toggle state changes when you step on it). Two further
factory presets (`data/05-Pure Clean (factory).prst`,
`data/01-Neo Soul (factory).prst`) and a user-built one
(`data/56-IX50 (user).prst`) corroborate the same mask/bit scheme.

**Implemented**: `gp50/preset.py`'s `create_preset()` now writes this
record via `_assign_footswitches()` — FS1 is bound to PRE and FS2 to DST
whenever that block type is present in the generated rig (regardless of
its own enabled/bypass flag, matching the confirmed independence above);
a slot with no matching block keeps whatever the template already has
there (the blank template's `0`/unassigned).

**The two trailing bytes are also confirmed and implemented**: a
derived LED-state cache, `byte = 5 + (1 if a block bound to that
footswitch is currently enabled, else 0)`. Verified against all 7 real
exports collected across 4 distinct presets:

| File | fs1 → | fs2 → | bytes 550,551 | matches formula |
|---|---|---|---:|---|
| `blank_gp50.prst` | — | — | `05 05` | yes (nothing assigned) |
| `56-IX50 (user).prst` | — | — | `05 05` | yes (nothing assigned) |
| `Mick Ronson Lead (1).prst` | — | — | `05 05` | yes (PRE/DST/DLY are *on*, but unassigned — proves the baseline isn't just "anything enabled") |
| `66-Mick Ronso (DST on).prst` | PRE (on) | DST (on) | `06 06` | yes |
| `66-Mick Ronso (DST off).prst` | PRE (on) | DST (off) | `06 05` | yes |
| `05-Pure Clean (factory).prst` | MOD (on) | RVB (on) | `06 06` | yes |
| `01-Neo Soul (factory).prst` | DST (on) | DLY+RVB (both on) | `06 06` | yes |

`gp50/preset.py`'s `_assign_footswitches()` computes and writes these
bytes from the final fs1/fs2 masks and the bypass mask, rather than
copying the template's (always-stale) value.

## 8. Params record

320 bytes = 80 × little-endian `float32`. Index `= block_index * 8 +
alg_id`, i.e. 8 parameter slots reserved per block (§3), addressed by each
parameter's `alg_id` (from `gp50_catalog.json`) regardless of how many
parameters that particular model actually uses. Both projects agree on the
320-byte size and per-block stride; this project's own `gp50_catalog.json`
is the source for which `alg_id` maps to which named control per model
(reverse-engineered separately — see `docs/GP50_MANUAL_V1_0_5_AUDIT.md` for
that catalogue's own audit against the firmware manual).

## 9. Open questions — do not guess further without new evidence

- **The order record's permutation invariant does not hold on a real Suite
  export.** drewmerc302's project asserts (and enforces in `patch/
  prst_format.py:is_permutation`/`write_order`) that this record is always
  a strict permutation of `0..9`, proven via live-hardware `0x41` read/write
  round-trips over WebMIDI. `data/Mick Ronson Lead (1).prst`'s order
  record, `[2, 1, 7, 9, 3, 4, 5, 6, 7, 8]`, is **not** one: block index 7
  (DLY) appears twice (position 2 and its usual tail position 8), and block
  index 0 (NR) is entirely absent.

  An initial theory — that `order[0:3]` is simply that file's *enabled*
  block indices written to the front, with the rest left stale — is
  **disproven** by a controlled follow-up: two exports of the same preset
  (`66-Mick Ronso.prst` / `66-Mick Ronso(1).prst`) differing by exactly one
  bypass-mask bit (DST toggled on/off, confirmed by diffing them: only the
  CRC, the bypass byte, and one unrelated trailing byte changed) have
  **byte-identical order records**, `[2, 1, 7, 9, 3, 4, 5, 6, 7, 8]`, in
  both. If the prefix reflected live enabled/bypass state, toggling DST off
  should have dropped it from the prefix — it didn't. So order and bypass
  are confirmed independent fields: order is static per saved chain
  arrangement (consistent with drewmerc302's live-hardware "deliberate
  drag-reorder" model, a different code path from routine bypass toggling),
  not a live reflection of what's currently on. Why *this specific*
  preset's saved arrangement isn't a valid permutation is still open —
  ruling out the enabled-prefix theory narrows it, but doesn't resolve it.
  A second theory — that factory-authored presets skip whatever live-device
  validation enforces the permutation invariant, so it'd only hold for
  presets actually built/reordered on-device — is also **disproven**:
  `data/05-Pure Clean (factory).prst` and `data/01-Neo Soul (factory).prst`
  are both genuine factory presets (user-confirmed) with many blocks
  enabled, and both have the exact clean default order,
  `[0, 1, 2, 9, 3, 4, 5, 6, 7, 8]` — identical to blank's. So does
  `data/56-IX50 (user).prst`, a preset the user built themselves. Of **7**
  real exports now collected, across **6 distinct presets**, exactly **one**
  preset (the "Mick Ronson"/slot-66 one, seen 3 times) has a non-permutation
  order record; every other one — factory or user-built, many blocks
  enabled or none — matches blank's untouched default exactly. This means
  chain reordering essentially never happens in practice (nobody drags
  blocks around, factory content included), and whatever produced this one
  preset's unusual value is a **one-off**, not a general Suite-export or
  factory-content behavior.

  A "the user manually drag-reordered this one" theory was the natural next
  guess, but the user does not recall ever doing so for this preset — and
  reports that the one preset they're certain they built and reordered
  themselves (`data/56-IX50 (user).prst`) has the clean default order, not
  a scrambled one. So a deliberate reorder isn't a confirmed explanation
  either; this remains a genuine, unresolved one-off. **Stays open** with
  no further lead at this point — closing it would need either a live
  device read of that exact preset (bypassing Suite's export, to check
  whether the live protocol reports the same non-permutation value or a
  clean one) or another preset that happens to reproduce the same pattern.
  Not investigated further absent new evidence. This project's response is
  unaffected either way: §5's policy is to never write to this record.

- **SnapTone's binary payload encoding is unconfirmed.** Both projects agree
  the models/bypass/order/params records don't encode SnapTone capture data
  itself — drewmerc302's `re/SNAPTONE_PROTOCOL.md` reverse-engineers the
  *live SysEx write protocol* for importing a capture (packet framing,
  per-block CRC) but explicitly leaves "how the ~2.7 KB SnapTone payload
  itself is produced from a NAM" as unsolved. This project's `gp50/preset.py`
  deliberately never writes SnapTone model data for this reason — a selected
  SnapTone slot is shown to the user for review but not written into the
  `N->S` model record.

## 10. Cross-checking this document

Everything attributed to drewmerc302's project above can be re-verified
directly: `curl -sL https://raw.githubusercontent.com/drewmerc302/valeton-gp50/master/patch/prst_format.py`
is stdlib-only and can be run against `data/blank_gp50.prst` and
`data/Mick Ronson Lead (1).prst` directly (as this write-up's own research
did) to reproduce every decoded value in this document.
