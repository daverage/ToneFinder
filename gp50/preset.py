"""Template-preserving GP-50 .prst editor.

Only documented records are altered. A known-good blank preset is mandatory.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from .catalog import GP50Catalog, canonical_module, default_catalog
from .validator import validate_rig

FILE_SIZE = 552
NAME_OFFSET, NAME_SIZE, BODY_OFFSET = 0x19, 16, 0x29
# The field is 16 bytes wide, but Suite-exported files use the final byte as a
# NUL terminator. Writing all 16 bytes leaves no terminator and causes Suite to
# discard the displayed name.
NAME_TEXT_SIZE = NAME_SIZE - 1
RECORDS = {"models": bytes.fromhex("03302800"), "bypass": bytes.fromhex("01300400"),
           "order": bytes.fromhex("02300A00"), "params": bytes.fromhex("04304001"),
           "footswitches": bytes.fromhex("03000A00")}


class PresetError(ValueError): pass


# The `order` record is NOT footswitch assignment — that was this module's
# original guess from a single byte-diff, and it was wrong. It's the DSP
# signal-chain order: order[chain_position] = the block's fixed module_id.
# Confirmed independently by drewmerc302/valeton-gp50's live-hardware
# read-diff (re/DEVICE_BLOCKORDER.md, 2026-07-16): dragging one block to a
# new chain position on a real unit and re-reading changed only these 10
# bytes (plus the CRC) — nothing else, and definitely not any footswitch
# behavior. Their finding: DST/N->S/AMP/CAB/EQ form a fixed-relative-order,
# always-contiguous "core" run; NR/PRE/MOD/DLY/RVB are "movable" and can sit
# anywhere before/after it. The blank template's default order
# ([0,1,2,9,3,4,5,6,7,8]) already places the movables in the conventional
# pedalboard arrangement — gate/comp/drive before the amp, modulation/delay/
# reverb after — so simply leaving this record untouched (as create_preset
# now does) gives every generated preset a correct, standard signal order
# for free, with no risk of writing a value real hardware would reject.
#
# This module previously computed a "footswitch" reassignment onto the
# `order` bytes by swapping module_ids into position 0/1 for whichever
# pedal was enabled — based on a single earlier example that, per the
# confirmed spec above, was actually just a chain reorder that happened to
# look plausible as footswitch binding. That was removed rather than
# repurposed: `order` has nothing to do with footswitches.
#
# Real footswitch binding lives in a separate trailer record (magic
# `03 00 0A 00`): [fs1 u32 mask][fs2 u32 mask][2 trailing bytes, unknown —
# left untouched]. Each mask's bit `k` (block index, see catalog.module_id)
# means that footswitch toggles block `k`'s bypass state; docs/
# GP50_PRST_FORMAT.md records real examples with 0-2 bits set. Confirmed
# against two real Suite exports of the same preset with FS2 physically
# pressed on the real device between them (user-confirmed, not inferred):
# both have fs1=PRE, fs2=DST, byte-identical whether DST's *bypass* bit is
# on or off — i.e. the assignment is independent of the block's current
# on/off state, exactly like a real pedalboard footswitch binding should
# be. This module assigns FS1/FS2 to whichever of PRE/DST is present in
# the generated rig (regardless of its enabled/bypass state) so the two
# most commonly footswitched effect types are live-toggleable on the real
# unit; a slot with no matching block keeps whatever the template already
# has there.
#
# The record's last 2 bytes (one per footswitch) are a derived LED-state
# cache, not independent data: confirmed against 7 real exports across 4
# distinct presets (blank/unassigned, a user preset with no footswitches,
# and three presets with footswitches bound — one to two blocks per switch)
# — `byte = 5 + (1 if a block bound to that footswitch is currently
# enabled/on)`. Fully derivable from the bypass mask and the fs1/fs2 masks
# above, so this module computes and writes it rather than copying the
# template's stale value.
FOOTSWITCH_ASSIGNMENTS = {"fs1": "PRE", "fs2": "DST"}
_FOOTSWITCH_LED_BASELINE = 5


def _assign_footswitches(data: bytearray, offset: int, rig: dict[str, Any], catalog: GP50Catalog, enabled_mask: int) -> None:
    present = {canonical_module(block["module"]) for block in rig["signal_chain"]}
    for i, module in enumerate(FOOTSWITCH_ASSIGNMENTS.values()):
        if module in present:
            struct.pack_into("<I", data, offset + i * 4, 1 << catalog.module_id(module))
        # Recompute the LED byte from whatever mask ends up in the file
        # (ours, or the template's preserved one if we left it alone), not
        # just this call's own assignment decision.
        final_mask = struct.unpack_from("<I", data, offset + i * 4)[0]
        data[offset + 8 + i] = _FOOTSWITCH_LED_BASELINE + (1 if final_mask & enabled_mask else 0)


def crc8_07(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8): crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _record_offset(data: bytearray, marker: bytes, payload_size: int) -> int:
    position = data.find(marker, BODY_OFFSET)
    if position < 0 or position + len(marker) + payload_size > len(data):
        raise PresetError(f"Blank preset does not contain expected {marker.hex()} record")
    return position + len(marker)


def create_preset(plan: dict[str, Any], template: str | Path | None = None, catalog: GP50Catalog | None = None) -> bytes:
    catalog = catalog or default_catalog()
    rig = validate_rig(plan, catalog)
    path = Path(template) if template else Path(__file__).parents[1] / "data" / "blank_gp50.prst"
    if not path.is_file():
        raise PresetError(f"Blank GP-50 template is required at {path}. Export an empty preset from Valeton Suite; unknown bytes are intentionally preserved.")
    data = bytearray(path.read_bytes())
    if len(data) != FILE_SIZE: raise PresetError(f"Blank preset must be {FILE_SIZE} bytes, got {len(data)}")
    models = _record_offset(data, RECORDS["models"], 40)
    bypass = _record_offset(data, RECORDS["bypass"], 4)
    order = _record_offset(data, RECORDS["order"], 10)
    params = _record_offset(data, RECORDS["params"], 320)
    footswitches = _record_offset(data, RECORDS["footswitches"], 10)
    if sorted(data[order:order + 10]) != list(range(10)):
        raise PresetError("Blank preset has an invalid GP-50 chain-order record")
    encoded_name = rig["preset_name"].encode("latin-1", errors="replace")[:NAME_TEXT_SIZE]
    data[NAME_OFFSET:NAME_OFFSET + NAME_SIZE] = encoded_name.ljust(NAME_SIZE, b"\0")
    enabled_mask = 0
    for block in rig["signal_chain"]:
        effect = catalog.get(block["fxid"])
        # module_id is the catalogue-confirmed binary block slot. It is not the
        # UI signal-chain position (notably, N->S has module_id 9).
        block_index = int(effect["module_id"])
        module = canonical_module(block["module"])
        # SnapTone slot encoding is unknown: preserve template model and only enable it if requested.
        if block and module != "N->S": struct.pack_into("<I", data, models + block_index * 4, block["fxid"])
        if block and block["enabled"]:
            enabled_mask |= 1 << block_index
        for parameter in effect["params"]:
            if parameter["name"] in block["parameters"]:
                struct.pack_into("<f", data, params + 4 * (block_index * 8 + parameter["alg_id"]), block["parameters"][parameter["name"]])
    struct.pack_into("<I", data, bypass, enabled_mask)
    # The chain-order record is left exactly as the template has it — see
    # the note above the `order` variable's original footswitch-assignment
    # code (removed): the template's default order is already a correct,
    # conventional signal chain, and there is no per-request signal to
    # justify writing a different one.
    _assign_footswitches(data, footswitches, rig, catalog, enabled_mask)
    data[0x14] = crc8_07(bytes(data[0x15:]))
    return bytes(data)
