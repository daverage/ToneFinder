import struct
import tempfile
import unittest
from pathlib import Path

from gp50.catalog import GP50Catalog
from gp50.preset import FILE_SIZE, RECORDS, create_preset, crc8_07
from gp50.validator import RigValidationError, validate_rig
from app import preset_filename


class GP50Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = GP50Catalog(Path(__file__).parents[1] / "gp50_catalog.json")
        cls.effect = cls.catalog.effects_for_modules(["DST"])["DST"][0]

    def plan(self, value=50):
        return {"preset_name": "Unit Test", "signal_chain": [{"module": "DST", "fxid": self.effect["fxid"], "enabled": True, "purpose": "test", "parameters": {"Gain": value}}]}

    def test_catalogue_lookup(self):
        self.assertEqual(self.catalog.get(self.effect["fxid"])["module"], "DST")

    def test_compact_for_prompt_narrows_large_modules_by_relevance(self):
        full = self.catalog.compact_for_prompt(["AMP", "CAB", "DST"])
        self.assertGreater(len(full["modules"]["AMP"]), 12)
        self.assertGreater(len(full["modules"]["CAB"]), 12)

        narrowed = self.catalog.compact_for_prompt(["AMP", "CAB", "DST"], terms={"marshall", "jcm800"})
        self.assertLessEqual(len(narrowed["modules"]["AMP"]), 12)
        self.assertLessEqual(len(narrowed["modules"]["CAB"]), 12)
        # DST (10 models) is already under the cap and must be untouched.
        self.assertEqual(len(narrowed["modules"]["DST"]), len(full["modules"]["DST"]))
        # The matching amp should be surfaced, not scored out by the cap.
        amp_names = [e["name"] for e in narrowed["modules"]["AMP"]]
        self.assertIn("UK 800", amp_names)  # the GP-50's Marshall JCM800 model

    def test_preset_filename_is_ascii_alphanumeric(self):
        self.assertEqual(preset_filename("The Verve - Don't!"), "TheVerveDont.prst")
        self.assertEqual(preset_filename("***"), "gp50preset.prst")

    def test_clamps_and_steps(self):
        plan = validate_rig(self.plan(-20), self.catalog)
        self.assertEqual(plan["signal_chain"][0]["parameters"]["Gain"], 0.0)

    def test_normalizes_unambiguous_parameter_spelling(self):
        amp = next(e for e in self.catalog.effects_for_modules(["AMP"])["AMP"] if e["name"] == "Foxy 30TB")
        plan = validate_rig({"preset_name": "Foxy", "signal_chain": [{
            "module": "AMP", "fxid": amp["fxid"], "enabled": True,
            "purpose": "test", "parameters": {"Tone_Cut": 42},
        }]}, self.catalog)
        params = plan["signal_chain"][0]["parameters"]
        self.assertEqual(params["Tone Cut"], 42.0)
        # Every parameter the plan didn't set is filled from the catalogue's
        # own documented default, not left out (see validate_rig) — an
        # omitted parameter must not silently keep whatever byte the blank
        # preset template happens to have at that offset.
        for param in amp["params"]:
            if param["name"] != "Tone Cut":
                self.assertEqual(params[param["name"]], param["default"])

    def test_yellow_od_matches_the_manual_and_native_export_slots(self):
        effect = next(e for e in self.catalog.effects_for_modules(["DST"])["DST"] if e["name"] == "Yellow OD")
        self.assertEqual([(p["name"], p["alg_id"]) for p in effect["params"]], [("Gain", 0), ("Tone", 1), ("VOL", 2)])

    def test_rejects_invalid_parameter(self):
        plan = self.plan(); plan["signal_chain"][0]["parameters"] = {"Imaginary": 1}
        with self.assertRaises(RigValidationError): validate_rig(plan, self.catalog)

    def test_template_serialization(self):
        # A minimal record-valid fixture; real use requires an exported blank preset.
        raw = bytearray(FILE_SIZE)
        offsets = {"models": 64, "bypass": 112, "order": 124, "params": 144, "footswitches": 480}
        for name, pos in offsets.items(): raw[pos:pos + 4] = RECORDS[name]
        raw[128:138] = bytes(range(10))
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "blank.prst"; template.write_bytes(raw)
            output = create_preset(self.plan(), template, self.catalog)
        self.assertEqual(len(output), FILE_SIZE)
        self.assertEqual(output[0x19:0x29].rstrip(b"\0"), b"Unit Test")
        self.assertEqual(output[0x28], 0)
        self.assertEqual(output[64 + 4 + 2 * 4:64 + 4 + 3 * 4], struct.pack("<I", self.effect["fxid"]))
        self.assertEqual(struct.unpack_from("<I", output, 112 + 4)[0], 1 << 2)
        self.assertEqual(struct.unpack_from("<f", output, 144 + 4 + 4 * (2 * 8))[0], 50.0)
        self.assertEqual(output[0x14], crc8_07(output[0x15:]))

    def test_preset_name_reserves_a_null_terminator(self):
        # 10 characters is validate_rig's max (see its comment: Valeton
        # Suite's own preset browser silently truncates/blanks names longer
        # than 10, even though this 16-byte binary field and the GP-50's own
        # screen both handle up to 15 usable characters fine).
        plan = self.plan(); plan["preset_name"] = "TenLetters"
        raw = bytearray(FILE_SIZE)
        offsets = {"models": 64, "bypass": 112, "order": 124, "params": 144, "footswitches": 480}
        for name, pos in offsets.items(): raw[pos:pos + 4] = RECORDS[name]
        raw[128:138] = bytes(range(10))
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "blank.prst"; template.write_bytes(raw)
            output = create_preset(plan, template, self.catalog)
        self.assertEqual(output[0x19:0x23], b"TenLetters")
        self.assertEqual(output[0x23], 0)

    def _order_template(self, directory):
        """A minimal blank template with just the five binary records this
        module writes to, at the same offsets create_preset locates by marker.
        """
        raw = bytearray(FILE_SIZE)
        offsets = {"models": 64, "bypass": 112, "order": 124, "params": 144, "footswitches": 480}
        for name, pos in offsets.items(): raw[pos:pos + 4] = RECORDS[name]
        raw[128:138] = bytes([0, 1, 2, 9, 3, 4, 5, 6, 7, 8])
        template = Path(directory) / "blank.prst"
        template.write_bytes(raw)
        return template

    def test_order_record_is_always_left_exactly_as_the_template_has_it(self):
        """The `order` record is the DSP chain order (independently confirmed
        against a real Suite export and drewmerc302/valeton-gp50's
        hardware-verified reverse-engineering — see docs/GP50_PRST_FORMAT.md),
        not footswitch assignment as an earlier version of this module
        guessed. create_preset never writes to it: the template's default
        order is already a correct, conventional signal chain (movable
        gate/comp/drive blocks before the fixed amp/cab/eq core, modulation/
        delay/reverb after), and there is no per-request signal to justify
        writing a different one. This must hold regardless of which modules
        the plan enables.
        """
        dst = self.catalog.effects_for_modules(["DST"])["DST"][0]
        pre = self.catalog.effects_for_modules(["PRE"])["PRE"][0]
        mod = self.catalog.effects_for_modules(["MOD"])["MOD"][0]
        dly = self.catalog.effects_for_modules(["DLY"])["DLY"][0]
        plan = {"preset_name": "Order", "signal_chain": [
            {"module": "DST", "fxid": dst["fxid"], "enabled": True, "parameters": {}},
            {"module": "PRE", "fxid": pre["fxid"], "enabled": True, "parameters": {}},
            {"module": "MOD", "fxid": mod["fxid"], "enabled": True, "parameters": {}},
            {"module": "DLY", "fxid": dly["fxid"], "enabled": True, "parameters": {}},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            output = create_preset(plan, self._order_template(directory), self.catalog)
        self.assertEqual(output[128:138], bytes([0, 1, 2, 9, 3, 4, 5, 6, 7, 8]))

    def test_real_suite_export_ground_truth(self):
        # Ground-truth fixture: a real Valeton Suite export ("Mick Ronson
        # Lead") with PRE=C-Wah, DST=Red Haze (fuzz), DLY=Tape actually
        # enabled (bypass bitmask bits 1,2,7). Pins down the byte-level
        # facts this project's understanding of the format is built on —
        # see docs/GP50_PRST_FORMAT.md for the full write-up, including the
        # open question this file raises: drewmerc302/valeton-gp50's
        # hardware-read-diff reverse-engineering (re/DEVICE_BLOCKORDER.md)
        # found the `order` record is always a strict permutation of 0..9
        # on a live device, but this Suite-exported file's order,
        # [2, 1, 7, 9, 3, 4, 5, 6, 7, 8], is NOT one — module_id 7 (DLY)
        # appears at both position 2 and its usual tail position 8, and
        # module_id 0 (NR) is entirely absent. An "order[0:3] = currently
        # enabled blocks" theory was disproven by a controlled follow-up —
        # see test_order_record_is_independent_of_bypass_state below — so
        # this stays a genuinely open discrepancy; this module treats it as
        # a reason to never write to this record rather than guess further.
        path = Path(__file__).parents[1] / "data" / "Mick Ronson Lead (1).prst"
        if not path.is_file():
            self.skipTest("Real Suite export sample not present in data/")
        raw = path.read_bytes()
        bypass_pos = raw.find(RECORDS["bypass"]) + len(RECORDS["bypass"])
        (bypass_mask,) = struct.unpack_from("<I", raw, bypass_pos)
        self.assertEqual(bypass_mask, (1 << self.catalog.module_id("PRE")) | (1 << self.catalog.module_id("DST")) | (1 << self.catalog.module_id("DLY")))
        order_pos = raw.find(RECORDS["order"]) + len(RECORDS["order"])
        self.assertEqual(raw[order_pos:order_pos + 10], bytes([2, 1, 7, 9, 3, 4, 5, 6, 7, 8]))

    def test_order_record_is_independent_of_bypass_state(self):
        # A controlled pair of real Suite exports of the *same* preset,
        # differing by exactly one change (DST toggled on/off — confirmed
        # below: only the CRC, the bypass byte, and one unrelated trailing
        # byte differ). This disproves the theory that `order[0:3]` tracks
        # which blocks are currently enabled: if it did, toggling DST off
        # would drop it from the front. It doesn't move at all. See
        # docs/GP50_PRST_FORMAT.md section 8 for the full write-up.
        on_path = Path(__file__).parents[1] / "data" / "66-Mick Ronso (DST on).prst"
        off_path = Path(__file__).parents[1] / "data" / "66-Mick Ronso (DST off).prst"
        if not (on_path.is_file() and off_path.is_file()):
            self.skipTest("Real Suite export A/B sample not present in data/")
        dst_on = on_path.read_bytes()
        dst_off = off_path.read_bytes()

        def bypass_mask(raw):
            pos = raw.find(RECORDS["bypass"]) + len(RECORDS["bypass"])
            (mask,) = struct.unpack_from("<I", raw, pos)
            return mask

        def order_bytes(raw):
            pos = raw.find(RECORDS["order"]) + len(RECORDS["order"])
            return raw[pos:pos + 10]

        dst_bit = 1 << self.catalog.module_id("DST")
        self.assertTrue(bypass_mask(dst_on) & dst_bit)
        self.assertFalse(bypass_mask(dst_off) & dst_bit)
        # Everything else about the bypass mask (which other blocks are
        # enabled) is unchanged — this really isolates the DST bit alone.
        self.assertEqual(bypass_mask(dst_on) & ~dst_bit, bypass_mask(dst_off) & ~dst_bit)
        self.assertEqual(order_bytes(dst_on), order_bytes(dst_off))

    def test_real_footswitch_trailer_matches_the_confirmed_user_report(self):
        # Ground truth: the user physically pressed footswitch 2 on the real
        # unit to toggle DST between these two exports. Decoding the trailer
        # record (magic 03 00 0A 00, [fs1 u32][fs2 u32][2 unknown bytes])
        # gives fs1=PRE, fs2=DST in both files — matching the report exactly,
        # and confirming the assignment is unaffected by the block's own
        # bypass state (see the previous test). This is the record
        # create_preset now writes via _assign_footswitches.
        on_path = Path(__file__).parents[1] / "data" / "66-Mick Ronso (DST on).prst"
        off_path = Path(__file__).parents[1] / "data" / "66-Mick Ronso (DST off).prst"
        if not (on_path.is_file() and off_path.is_file()):
            self.skipTest("Real Suite export A/B sample not present in data/")
        for raw in (on_path.read_bytes(), off_path.read_bytes()):
            pos = raw.find(RECORDS["footswitches"]) + len(RECORDS["footswitches"])
            fs1, fs2 = struct.unpack_from("<II", raw, pos)
            self.assertEqual(fs1, 1 << self.catalog.module_id("PRE"))
            self.assertEqual(fs2, 1 << self.catalog.module_id("DST"))

    def test_create_preset_assigns_fs1_to_pre_and_fs2_to_dst_when_present(self):
        pre = self.catalog.effects_for_modules(["PRE"])["PRE"][0]
        dst = self.catalog.effects_for_modules(["DST"])["DST"][0]
        plan = {"preset_name": "FS Test", "signal_chain": [
            {"module": "PRE", "fxid": pre["fxid"], "enabled": True, "parameters": {}},
            {"module": "DST", "fxid": dst["fxid"], "enabled": False, "parameters": {}},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            output = create_preset(plan, self._order_template(directory), self.catalog)
        pos = output.find(RECORDS["footswitches"]) + len(RECORDS["footswitches"])
        fs1, fs2 = struct.unpack_from("<II", output, pos)
        self.assertEqual(fs1, 1 << self.catalog.module_id("PRE"))
        # Assignment tracks presence in the chain, not the enabled/bypass
        # flag — DST is disabled here but still gets FS2, matching the real
        # device's behaviour (assignment persists independent of on/off
        # state; see test_order_record_is_independent_of_bypass_state).
        self.assertEqual(fs2, 1 << self.catalog.module_id("DST"))

    def test_create_preset_leaves_footswitches_untouched_without_pre_or_dst(self):
        amp = self.catalog.effects_for_modules(["AMP"])["AMP"][0]
        plan = {"preset_name": "No FS", "signal_chain": [
            {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "parameters": {}},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            output = create_preset(plan, self._order_template(directory), self.catalog)
        pos = output.find(RECORDS["footswitches"]) + len(RECORDS["footswitches"])
        self.assertEqual(struct.unpack_from("<II", output, pos), (0, 0))

    def test_footswitch_led_bytes_match_confirmed_real_hardware_formula(self):
        # byte = 5 + (1 if a block bound to that footswitch is currently
        # enabled) — confirmed against 7 real Suite exports across 4 distinct
        # presets (data/*.prst), including a footswitch bound to two blocks
        # at once. See docs/GP50_PRST_FORMAT.md section 7.
        pre = self.catalog.effects_for_modules(["PRE"])["PRE"][0]
        dst = self.catalog.effects_for_modules(["DST"])["DST"][0]

        def trailing_bytes(dst_enabled):
            plan = {"preset_name": "FS", "signal_chain": [
                {"module": "PRE", "fxid": pre["fxid"], "enabled": True, "parameters": {}},
                {"module": "DST", "fxid": dst["fxid"], "enabled": dst_enabled, "parameters": {}},
            ]}
            with tempfile.TemporaryDirectory() as directory:
                output = create_preset(plan, self._order_template(directory), self.catalog)
            pos = output.find(RECORDS["footswitches"]) + len(RECORDS["footswitches"])
            return output[pos + 8], output[pos + 9]

        self.assertEqual(trailing_bytes(dst_enabled=True), (6, 6))
        self.assertEqual(trailing_bytes(dst_enabled=False), (6, 5))

        with tempfile.TemporaryDirectory() as directory:
            output = create_preset({"preset_name": "No FS", "signal_chain": []}, self._order_template(directory), self.catalog)
        pos = output.find(RECORDS["footswitches"]) + len(RECORDS["footswitches"])
        self.assertEqual((output[pos + 8], output[pos + 9]), (5, 5))

    def test_real_footswitch_led_bytes_across_four_presets(self):
        # Ground truth for the LED-byte formula: blank/unassigned, a user
        # preset with no footswitches, and two factory presets each with a
        # footswitch bound to a currently-on block (one bound to two blocks
        # at once). All 7 real exports observed agree with the formula;
        # these 4 files are the ones kept in the repo as fixtures.
        cases = [
            ("blank_gp50.prst", (5, 5)),
            ("Mick Ronson Lead (1).prst", (5, 5)),
            ("66-Mick Ronso (DST on).prst", (6, 6)),
            ("66-Mick Ronso (DST off).prst", (6, 5)),
        ]
        for filename, expected in cases:
            path = Path(__file__).parents[1] / "data" / filename
            if not path.is_file():
                continue
            raw = path.read_bytes()
            pos = raw.find(RECORDS["footswitches"]) + len(RECORDS["footswitches"])
            self.assertEqual((raw[pos + 8], raw[pos + 9]), expected, filename)


if __name__ == "__main__": unittest.main()
