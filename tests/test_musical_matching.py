import unittest
from pathlib import Path

from gp50.catalog import GP50Catalog, descriptor_relevance, score_effect_relevance, tokenize


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

    def test_has_device_named_finds_a_real_pedal_by_origin(self):
        self.assertTrue(self.catalog.has_device_named("Dunlop Cry Baby Wah"))

    def test_has_device_named_rejects_a_single_word_coincidence(self):
        # "Talk Box" has no catalogue equivalent at all, but shares the word
        # "box" with an unrelated pedal ("La Charger", origin "MI Audio
        # Crunch Box") -- a bare intersection would wrongly call this a
        # match (confirmed: it did, before the majority-overlap fix).
        self.assertFalse(self.catalog.has_device_named("Talk Box"))
        self.assertFalse(self.catalog.has_device_named("nonexistent gizmo"))
        self.assertFalse(self.catalog.has_device_named(""))

    def test_descriptor_relevance_ignores_a_brand_mentioned_only_in_passing(self):
        # "TalkBox ... vocal-like guitar sound (Heil Sound or MXR)": the
        # request never asked for an MXR product, but score_effect_relevance
        # (which rewards an exact origin/keyword identity match) ranks an
        # unrelated MXR Phase 90-style phaser above the catalogue's actually
        # vocal-character wah/envelope-filter effects purely on the word
        # "mxr" — a real false positive this catalogue reproduces, not a
        # hypothetical. descriptor_relevance (character/keywords/roles only,
        # no identity bonus) must not make the same mistake.
        #
        # tokenize()'s own stopword list doesn't drop "guitar"/"sound" (its
        # actual caller, gp50.rig_builder._fallback_module_for_effect, strips
        # those and more via its own _FALLBACK_STOPWORDS first) — stripped
        # here too, so this test reflects the terms the real caller passes,
        # not a diluted set that hides the false positive behind generic-word
        # noise.
        #
        # No type_profile is passed below, matching how gp50.rig_builder's
        # scoring call sites actually call this (e.g.
        # _fallback_module_for_effect, _resolve_missing_fxid, _best_amp) —
        # each effect's own musical_profile only, not the merged generic
        # type-level profile.
        terms = tokenize("talkbox achieve iconic vocal-like guitar sound heil sound or mxr") - {"guitar", "sound"}
        o_phase = next(e for e in self.catalog.effects_for_modules(["MOD"])["MOD"] if e["name"] == "O-Phase")
        c_wah = next(e for e in self.catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "C-Wah")

        full_score_o_phase = score_effect_relevance(o_phase, terms)
        full_score_c_wah = score_effect_relevance(c_wah, terms)
        self.assertGreater(full_score_o_phase, full_score_c_wah, "sanity check: the false positive is real")

        descriptor_o_phase = descriptor_relevance(o_phase, terms)
        descriptor_c_wah = descriptor_relevance(c_wah, terms)
        self.assertGreater(descriptor_c_wah, descriptor_o_phase)


if __name__ == "__main__":
    unittest.main()
