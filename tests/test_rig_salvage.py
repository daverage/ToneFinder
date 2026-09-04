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
        # With no purpose text or payload to score against, both candidates
        # tie at 0 relevance (`_keep_best_per_module`'s tie-break keeps
        # whichever was seen first) — see
        # test_a_higher_relevance_candidate_wins_a_shared_slot below for the
        # case where the candidates actually differ.
        catalog = GP50Catalog()
        reverbs = catalog.effects_for_modules(["RVB"])["RVB"]
        rig = _salvage_valid_blocks({"preset_name": "Test", "signal_chain": [
            {"module": "RVB", "fxid": reverbs[0]["fxid"], "enabled": True, "parameters": {}},
            {"module": "RVB", "fxid": reverbs[1]["fxid"], "enabled": True, "parameters": {}},
        ]}, catalog)
        self.assertEqual(len(rig["signal_chain"]), 1)
        self.assertEqual(rig["signal_chain"][0]["fxid"], reverbs[0]["fxid"])

    def test_a_higher_relevance_candidate_wins_a_shared_slot(self):
        # A tone can legitimately call for several PRE-type roles at once —
        # a wah, an octave texture, a solo boost — but PRE (like DST and
        # MOD) is one physical slot (GP50Catalog.shared_effect_slots). Given
        # several real candidates for the same module, keep whichever one's
        # own purpose text actually matches this request best, not whichever
        # was listed first — here the weakest match (Boost) is listed first,
        # deliberately, to prove order isn't what decides it.
        catalog = GP50Catalog()
        wah = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "C-Wah")
        octa = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "OCTA")
        boost = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "B-Boost")
        rig = _salvage_valid_blocks({"preset_name": "Test", "signal_chain": [
            {"module": "PRE", "fxid": boost["fxid"], "purpose": "slight solo lift", "enabled": True, "parameters": {}},
            {"module": "PRE", "fxid": octa["fxid"], "purpose": "octave-up texture on the outro", "enabled": True, "parameters": {}},
            {"module": "PRE", "fxid": wah["fxid"], "purpose": "classic funk rhythm wah cry baby sweep", "enabled": True, "parameters": {}},
        ]}, catalog, {"query": "funk rhythm wah guitar"})
        self.assertEqual(len(rig["signal_chain"]), 1)
        self.assertEqual(rig["signal_chain"][0]["effect_name"], "C-Wah")

    def test_a_wah_role_is_translated_without_an_fxid(self):
        # PRE hosts Comp/Boost/Pitch/Sim/Wah/Filter models on one shared
        # slot; _resolve_missing_fxid previously only recognized
        # compress/boost roles here (a hardcoded keyword->model table), so a
        # model that named a wah-type effect but omitted its fxid (a common
        # local-model failure mode) had no way to be salvaged at all — it
        # just silently disappeared. It's now resolved by scoring every PRE
        # candidate against the catalogue's own musical_profile data
        # (keywords/character/roles), the same mechanism _best_amp/
        # _matching_cab/_pick_reverb already use, instead of another
        # hand-maintained needle list — so any genuinely wah-flavored
        # purpose text finds a real catalogue wah/filter model on its own.
        catalog = GP50Catalog()
        rig = _salvage_valid_blocks({"preset_name": "Test", "signal_chain": [
            {"module": "PRE", "fxid": None, "purpose": "Classic cry baby wah pedal sweep", "enabled": True, "parameters": {}},
        ]}, catalog)
        self.assertIsNotNone(rig)
        self.assertEqual(rig["signal_chain"][0]["effect_name"], "C-Wah")

    def test_missing_preset_name_falls_back_to_the_request_text_not_a_fixed_default(self):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        rig = _salvage_valid_blocks(
            {"signal_chain": [{"module": "AMP", "fxid": amp["fxid"], "enabled": True, "parameters": {}}]},
            catalog, {"query": "warm bluesy lead"},
        )
        self.assertEqual(rig["preset_name"], "warm blues")


if __name__ == "__main__": unittest.main()
