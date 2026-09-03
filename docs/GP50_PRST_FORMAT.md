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

**Name field**: 16 bytes, but only the first 15 are usable — the final byte
must stay `\0` as a terminator, or Suite discards the whole field (confirmed
independently by this project; not mentioned by the external one).

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

Both projects agree on all four magics, sizes, and (for models/bypass/params)
semantics. This project's `gp50/preset.py:RECORDS` and drewmerc302's
`patch/prst_format.py:REC_MODELS/REC_BYPASS/REC_ORDER/REC_PARAMS` are
byte-identical definitions, arrived at independently.

drewmerc302's project additionally documents a **footswitch trailer** near
the end of the file, magic `03 00 0A 00` (GP-50) / `03 00 08 00` (GP-5),
holding `[FS1 u32][FS2 u32]` bitmasks plus 2 trailing bytes. This project has
not independently confirmed what that trailer actually contains — see §8.

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
*before* the core, movables MOD, DLY, RVB *after* it.

**This project's policy, given the above**: `gp50/preset.py:create_preset()`
no longer writes to the order record at all — it passes the template's
order straight through. The default is already a correct, conventional
signal chain for every rig this app builds (gate/comp/drive before the amp,
modulation/delay/reverb after), and there is no per-request signal (nothing
in the LLM's rig-building output describes a desired chain reorder) to
justify computing a different one. This sidesteps the open discrepancy in
§8 entirely rather than resolving it by guessing.

## 6. Bypass record

`u32` bitmask, bit `k` = block index `k` (§3) is active/enabled. Both
projects agree. Confirmed against `data/Mick Ronson Lead (1).prst`: mask
`0b10000110` = bits 1, 2, 7 set = PRE, DST, DLY — matching that preset's
actual audible effects (a wah, a fuzz, and a delay). AMP/CAB/N->S bits are
apparently never set in real exports (there is no "bypass the amp" concept)
— this project's own preset generation sets them anyway when marking those
blocks `enabled: true`, which appears to be harmless (the firmware likely
ignores those bits) but is not independently confirmed to be correct.

## 7. Params record

320 bytes = 80 × little-endian `float32`. Index `= block_index * 8 +
alg_id`, i.e. 8 parameter slots reserved per block (§3), addressed by each
parameter's `alg_id` (from `gp50_catalog.json`) regardless of how many
parameters that particular model actually uses. Both projects agree on the
320-byte size and per-block stride; this project's own `gp50_catalog.json`
is the source for which `alg_id` maps to which named control per model
(reverse-engineered separately — see `docs/GP50_MANUAL_V1_0_5_AUDIT.md` for
that catalogue's own audit against the firmware manual).

## 8. Open questions — do not guess further without new evidence

- **Where footswitch assignment actually lives is unconfirmed.**
  drewmerc302's project hypothesizes a trailer record (magic `03 00 0A 00`,
  two `u32` bitmasks) near the end of the file, but reading that offset in
  both of this project's sample files (`blank_gp50.prst` and "Mick Ronson
  Lead", the latter clearly a "live use" patch with pedals enabled) returned
  `0` for both masks in both files — i.e. no evidence either way from these
  two samples. This project does not attempt to read or write footswitch
  assignment at all, for either the plain "order record" theory this project
  originally (incorrectly) held, or the trailer theory. **If you have a real
  Suite export with a footswitch deliberately assigned, byte-diffing it
  against `data/blank_gp50.prst` is how to actually confirm this** (the same
  method used to confirm everything else in this document).

- **The order record's permutation invariant does not hold on a real Suite
  export — and the actual pattern points at Suite's export path itself,
  not the movable/core model.** drewmerc302's project asserts (and enforces
  in `patch/prst_format.py:is_permutation`/`write_order`) that this record
  is always a strict permutation of `0..9`, proven via live-hardware
  `0x41` read/write round-trips over WebMIDI. `data/Mick Ronson Lead
  (1).prst`'s order record, `[2, 1, 7, 9, 3, 4, 5, 6, 7, 8]`, is **not** one:
  block index 7 (DLY) appears twice (position 2 and its usual tail position
  8), and block index 0 (NR) is entirely absent. The pattern is exact,
  though, not random: `order[0:3]` is precisely that file's three *enabled*
  block indices (bypass mask bits 1, 2, 7 → PRE, DST, DLY), written to the
  front, and `order[3:10]` is byte-identical to the blank template's
  untouched tail — including the stale leftover copy of DLY's id at its
  normal position 7. The leading theory: Suite's static `.prst` **export**
  code writes the enabled blocks to the front and leaves whatever was
  already in the remaining slots untouched, rather than maintaining a true
  10-value permutation the way the live SysEx protocol does — i.e. this is
  plausibly a Suite-export-specific shortcut/quirk, not evidence against
  the movable/core model itself (which was proven on live hardware, a
  different code path). Unconfirmed from a single example; would need
  either a live device read of this same patch (bypassing Suite's export)
  or a second Suite export differing by exactly one enable/disable toggle
  to isolate the pattern further. This project's response is §5's policy
  either way: never write to this record.

- **SnapTone's binary payload encoding is unconfirmed.** Both projects agree
  the models/bypass/order/params records don't encode SnapTone capture data
  itself — drewmerc302's `re/SNAPTONE_PROTOCOL.md` reverse-engineers the
  *live SysEx write protocol* for importing a capture (packet framing,
  per-block CRC) but explicitly leaves "how the ~2.7 KB SnapTone payload
  itself is produced from a NAM" as unsolved. This project's `gp50/preset.py`
  deliberately never writes SnapTone model data for this reason — a selected
  SnapTone slot is shown to the user for review but not written into the
  `N->S` model record.

## 9. Cross-checking this document

Everything attributed to drewmerc302's project above can be re-verified
directly: `curl -sL https://raw.githubusercontent.com/drewmerc302/valeton-gp50/master/patch/prst_format.py`
is stdlib-only and can be run against `data/blank_gp50.prst` and
`data/Mick Ronson Lead (1).prst` directly (as this write-up's own research
did) to reproduce every decoded value in this document.
