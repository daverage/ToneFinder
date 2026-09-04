import unittest
from pathlib import Path

from gp50.catalog import GP50Catalog, score_effect_relevance, tokenize


class MusicalMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = GP50Catalog(Path(__file__).parents[1] / "gp50_catalog.json")

    def _rank(self, module: str, query: str):
        effects = self.catalog.effects_for_modules([module])[module]
        terms = tokenize(query)
        scored = [
            (score_effect_relevance(e, terms, self.catalog.type_profile(e["type"])), e["name"])
            for e in effects
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    def test_tube_screamer_style_query_favours_green_od(self):
        scored = self._rank("DST", "tight mid boost overdrive")
        by_name = dict((name, score) for score, name in scored)
        self.assertGreater(by_name["Green OD"], by_name["Red Haze"])
        self.assertGreater(by_name["Green OD"], by_name["Sora Fuzz"])
        self.assertEqual(scored[0][1], "Green OD")

    def test_british_rock_boost_query_surfaces_sd1_style_model(self):
        scored = self._rank("DST", "aggressive rock amp boost upper mids")
        by_name = dict((name, score) for score, name in scored)
        self.assertGreater(by_name["Super OD"], by_name["Red Haze"])
        self.assertGreater(by_name["Super OD"], 0)

    def test_dotted_eighth_query_favours_clean_digital_delay(self):
        scored = self._rank("DLY", "clear dotted eighth delay")
        by_name = dict((name, score) for score, name in scored)
        self.assertGreater(by_name["Pure"], by_name["Analog"])
        self.assertGreater(by_name["Pure"], by_name["Tape"])

    def test_warm_slapback_query_favours_analogue_delay(self):
        scored = self._rank("DLY", "warm slapback echo")
        by_name = dict((name, score) for score, name in scored)
        self.assertGreater(by_name["Analog"], by_name["Pure"])
        self.assertGreater(by_name["Sweet Echo"], by_name["Pure"])

    def test_open_rock_amp_query_favours_rock_amps_over_metal_or_pristine_clean(self):
        scored = self._rank("AMP", "open crunchy mid-forward dynamic rock")
        by_name = dict((name, score) for score, name in scored)
        self.assertGreater(by_name["UK 800"], by_name["EV 51"])
        self.assertGreater(by_name["UK 800"], by_name["J-120 CL"])

    def test_high_gain_metal_query_favours_high_gain_amps(self):
        scored = self._rank("AMP", "tight saturated high gain metal")
        by_name = dict((name, score) for score, name in scored)
        self.assertGreater(by_name["EV 51"], by_name["Dark Twin"])
        self.assertGreater(by_name["Mess DualM"], by_name["Tweedy"])

    def test_musical_profile_merges_generic_type_keywords_with_specific_ones(self):
        # Every Delay entry should stay matchable on the generic type keyword
        # "delay" even though it also carries its own specific keywords.
        pure = next(e for e in self.catalog.effects_for_modules(["DLY"])["DLY"] if e["name"] == "Pure")
        profile = self.catalog.musical_profile(pure)
        self.assertIn("delay", profile["keywords"])
        self.assertIn("dotted eighth", profile["keywords"])

    def test_rat_style_filter_control_has_inverted_semantic_hint(self):
        darktale = next(e for e in self.catalog.effects_for_modules(["DST"])["DST"] if e["name"] == "Darktale")
        filter_param = next(p for p in darktale["params"] if p["name"] == "Filter")
        self.assertIn("darker", filter_param["semantic"]["high"])

    def test_shared_effect_slots_finds_pre_dst_mod_as_multi_role_modules(self):
        # These three modules each hold several genuinely distinct effect
        # types (compressor vs. wah, overdrive vs. fuzz, chorus vs. tremolo)
        # on one physical GP-50 slot — a real hardware constraint, derived
        # here from the catalogue's own module/type data rather than a
        # hardcoded list, so it can't drift out of sync as the catalogue
        # changes. NR/EQ/DLY/RVB are homogeneous (one type each) and must
        # not appear.
        shared = self.catalog.shared_effect_slots()
        self.assertEqual(set(shared["PRE"]), {"Boost", "Comp", "Filter", "Pitch", "Sim", "Wah"})
        self.assertEqual(set(shared["DST"]), {"Bass Drive", "Distortion", "Fuzz", "OD"})
        self.assertEqual(set(shared["MOD"]), {"Chorus", "Flanger", "Phaser", "Tremolo", "Vibrato"})
        for homogeneous in ("NR", "EQ", "DLY", "RVB"):
            self.assertNotIn(homogeneous, shared)


if __name__ == "__main__":
    unittest.main()
