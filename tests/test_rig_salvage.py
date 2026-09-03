import unittest

from gp50.catalog import GP50Catalog
from gp50.rig_builder import _salvage_valid_blocks


class RigSalvageTests(unittest.TestCase):
    def test_discards_unknown_id_and_corrects_exact_module(self):
        catalog = GP50Catalog()
        delay = catalog.effects_for_modules(["DLY"])["DLY"][0]
        rig = _salvage_valid_blocks({"preset_name": "Test", "signal_chain": [
            {"module": "DST", "fxid": 32, "enabled": True, "parameters": {}},
            {"module": "DST", "fxid": delay["fxid"], "enabled": True, "parameters": {"Mix": 20}},
        ]}, catalog)
        self.assertEqual(len(rig["signal_chain"]), 1)
        self.assertEqual(rig["signal_chain"][0]["module"], "DLY")

    def test_block_missing_parameters_key_is_defaulted_not_dropped(self):
        # A local model sometimes omits `parameters` entirely rather than
        # sending an empty object; that used to raise a bare KeyError here
        # ("'parameters'") instead of falling through to a usable rig. The
        # resulting block's parameters are the catalogue's own documented
        # defaults for every control, not an empty dict — see validate_rig.
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        rig = _salvage_valid_blocks({"preset_name": "Test", "signal_chain": [
            {"module": "AMP", "fxid": amp["fxid"], "enabled": True},
        ]}, catalog)
        self.assertEqual(len(rig["signal_chain"]), 1)
        self.assertEqual(
            rig["signal_chain"][0]["parameters"],
            {p["name"]: p["default"] for p in amp["params"]},
        )

    def test_keeps_first_choice_for_a_single_slot_module(self):
        catalog = GP50Catalog()
        reverbs = catalog.effects_for_modules(["RVB"])["RVB"]
        rig = _salvage_valid_blocks({"preset_name": "Test", "signal_chain": [
            {"module": "RVB", "fxid": reverbs[0]["fxid"], "enabled": True, "parameters": {}},
            {"module": "RVB", "fxid": reverbs[1]["fxid"], "enabled": True, "parameters": {}},
        ]}, catalog)
        self.assertEqual(len(rig["signal_chain"]), 1)
        self.assertEqual(rig["signal_chain"][0]["fxid"], reverbs[0]["fxid"])


if __name__ == "__main__": unittest.main()
