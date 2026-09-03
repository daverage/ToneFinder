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
           "order": bytes.fromhex("02300A00"), "params": bytes.fromhex("04304001")}


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
# This module previously computed a "footswitch" reassignment here by
# swapping module_ids into position 0/1 for whichever pedal was enabled —
# based on a single earlier example that, per the confirmed spec above, was
# actually just a chain reorder that happened to look plausible as
# footswitch binding. That swap logic is removed rather than repurposed for
# chain reordering: rig_builder has no per-request signal about a desired
# non-default chain order to act on, and the default is already correct, so
# there's nothing to compute. Real footswitch binding (which physical
# switch toggles which block) is apparently stored elsewhere (see
# docs/GP50_PRST_FORMAT.md's open-questions section) and this module does
# not attempt to write it.


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
    # the note above the (removed) footswitch-assignment code this module
    # used to write here. The template's default order is already a
    # correct, conventional signal chain, and there is no per-request
    # signal to justify writing a different one.
    data[0x14] = crc8_07(bytes(data[0x15:]))
    return bytes(data)
