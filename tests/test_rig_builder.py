import unittest

from gp50.catalog import GP50Catalog, score_effect_relevance
from gp50.rig_builder import _best_amp, _matching_cab, _target_tone_from_intent, build_rig


class RigBuilderTests(unittest.TestCase):
    def test_schema_limits_a_model_to_catalogue_fxids(self):
        catalog = GP50Catalog()
        captured = {}

        def fake_lm(system, user, schema, **kwargs):
            captured["schema"] = schema
            captured["prompt"] = user
            effect = catalog.effects_for_modules(["DLY"])["DLY"][0]
            return {"preset_name": "Delay", "summary": "test", "signal_chain": [
                {"module": "DLY", "fxid": effect["fxid"], "enabled": True,
                 "purpose": "test", "parameters": {"Mix": 20, "Time": 300}}
            ]}

        rig = build_rig({"query": "delay", "intent": {"effects": [{"name": "Delay"}]}}, fake_lm, catalog)
        self.assertIn("DLY", {block["module"] for block in rig["signal_chain"]})
        ids = captured["schema"]["properties"]["signal_chain"]["items"]["properties"]["fxid"]["enum"]
        self.assertIn(rig["signal_chain"][0]["fxid"], ids)
        # Scoped to only the modules actually offered in the prompt (AMP,
        # CAB, RVB, DLY here — RVB is always offered alongside AMP/CAB, see
        # _relevant_modules), not every fxid in the whole catalogue — an id
        # from a genuinely unlisted module (e.g. MOD, not matched by this
        # delay-only intent) was never shown to the model, so it shouldn't
        # be schema-valid either, and a smaller enum is real
        # constrained-decoding overhead saved on every call.
        mod_id = catalog.effects_for_modules(["MOD"])["MOD"][0]["fxid"]
        self.assertNotIn(mod_id, ids)
        self.assertLess(len(ids), len(catalog.by_id))
        self.assertIn('"function": "Repeats the signal', captured["prompt"])
        self.assertIn('"musical_profile"', captured["prompt"])
        self.assertIn('"module": "AMP"', captured["prompt"])

    def test_builtin_mode_requires_and_returns_an_amp_and_cab(self):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Built In", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        rig = build_rig({"query": "warm clean", "use_builtin_amp_cab": True}, fake_lm, catalog)
        self.assertEqual({block["module"] for block in rig["signal_chain"]}, {"AMP", "CAB"})

    def test_builtin_mode_salvages_duplicate_single_slot_effects(self):
        catalog = GP50Catalog()
        amp = next(e for e in catalog.effects_for_modules(["AMP"])["AMP"] if e["name"] == "Foxy 30TB")
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        pre = catalog.effects_for_modules(["PRE"])["PRE"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Foxy", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {"Tone_Cut": 42}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "PRE", "fxid": pre["fxid"], "enabled": True, "purpose": "first", "parameters": {}},
                {"module": "PRE", "fxid": pre["fxid"], "enabled": True, "purpose": "duplicate", "parameters": {}},
            ]}

        rig = build_rig({"query": "voxy", "use_builtin_amp_cab": True}, fake_lm, catalog)
        # The duplicate PRE block is dropped (one block per module), and the
        # surviving blocks are returned in real GP-50 hardware signal order
        # (PRE before AMP/CAB — see MODULE_ORDER), not emission order.
        self.assertEqual([block["module"] for block in rig["signal_chain"]], ["PRE", "AMP", "CAB"])
        amp_block = next(b for b in rig["signal_chain"] if b["module"] == "AMP")
        self.assertEqual(amp_block["parameters"]["Tone Cut"], 42.0)

    def test_builtin_mode_uses_safe_amp_cab_when_model_omits_fxids(self):
        catalog = GP50Catalog()

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "A name that is much too long", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": None, "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": None, "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        rig = build_rig({"query": "Vox chime", "use_builtin_amp_cab": True}, fake_lm, catalog)
        self.assertEqual({block["module"] for block in rig["signal_chain"]}, {"AMP", "CAB"})
        self.assertLessEqual(len(rig["preset_name"].encode("latin-1")), 16)
        self.assertIn("did not provide valid", rig["validation_warning"])

    def test_builtin_mode_translates_known_effect_roles_without_fxids(self):
        catalog = GP50Catalog()

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Streets", "summary": "test", "signal_chain": [
                {"module": "PRE", "fxid": None, "enabled": True, "purpose": "Compressor for consistent attack and sustain", "parameters": {}},
                {"module": "DST", "fxid": None, "enabled": True, "purpose": "Light overdrive grit", "parameters": {}},
                {"module": "DLY", "fxid": None, "enabled": True, "purpose": "Long rhythmic delay repeats", "parameters": {}},
                {"module": "RVB", "fxid": None, "enabled": True, "purpose": "Large plate reverb", "parameters": {}},
            ]}

        rig = build_rig({"query": "U2 Streets", "use_builtin_amp_cab": True}, fake_lm, catalog)
        self.assertEqual({block["module"] for block in rig["signal_chain"]}, {"PRE", "DST", "AMP", "CAB", "DLY", "RVB"})
        names = {block["effect_name"] for block in rig["signal_chain"]}
        self.assertTrue({"COMP", "Green OD", "Pure", "Plate"}.issubset(names))

    def test_high_gain_plan_is_capped_and_gets_a_noise_gate(self):
        catalog = GP50Catalog()
        amp = next(effect for effect in catalog.effects_for_modules(["AMP"])["AMP"] if effect["name"] == "UK 800")
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        drive = next(effect for effect in catalog.effects_for_modules(["DST"])["DST"] if effect["name"] == "Green OD")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "High Gain", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {"Gain": 100, "VOL": 100}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "DST", "fxid": drive["fxid"], "enabled": True, "purpose": "tightener", "parameters": {"Gain": 100, "VOL": 100}},
            ]}

        rig = build_rig({"query": "high gain", "intent": {"gain": "high"}}, fake_lm, catalog)
        blocks = {block["module"]: block for block in rig["signal_chain"]}
        self.assertLessEqual(blocks["AMP"]["parameters"]["Gain"], 55)
        self.assertLessEqual(blocks["DST"]["parameters"]["Gain"], 35)
        self.assertEqual(blocks["NR"]["parameters"]["THRE"], 20)

    def test_delay_and_reverb_are_given_conservative_starting_settings(self):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        delay = catalog.effects_for_modules(["DLY"])["DLY"][0]
        reverb = next(effect for effect in catalog.effects_for_modules(["RVB"])["RVB"] if effect["name"] == "Plate")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Ambient", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "DLY", "fxid": delay["fxid"], "enabled": True, "purpose": "delay", "parameters": {"Mix": 80, "F.Back": 90, "Time": 900}},
                {"module": "RVB", "fxid": reverb["fxid"], "enabled": True, "purpose": "reverb", "parameters": {"Mix": 90, "Decay": 90}},
            ]}

        intent = {"effects": [{"name": "Delay", "purpose": "long rhythmic repeats"}, {"name": "Plate reverb", "purpose": "large space"}]}
        rig = build_rig({"query": "ambient", "intent": intent}, fake_lm, catalog)
        blocks = {block["module"]: block for block in rig["signal_chain"]}
        self.assertLessEqual(blocks["DLY"]["parameters"]["Mix"], 30)
        feedback = blocks["DLY"]["parameters"].get("F.Back", blocks["DLY"]["parameters"].get("Feedback"))
        self.assertLessEqual(feedback, 45)
        self.assertLessEqual(blocks["RVB"]["parameters"]["Mix"], 28)
        self.assertLessEqual(blocks["RVB"]["parameters"]["Decay"], 55)
        self.assertTrue(rig["effect_review"])

    def test_reference_settings_are_applied_to_the_matching_amp_only(self):
        catalog = GP50Catalog()
        jcm800 = next(e for e in catalog.effects_for_modules(["AMP"])["AMP"] if e["name"] == "UK 800")
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Sourced", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": jcm800["fxid"], "enabled": True, "purpose": "amp", "parameters": {"Gain": 10, "Treble": 10}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        # A published recipe for the exact real amp the model chose (its
        # `origin` is "Marshall JCM800") — these numbers should land on the
        # AMP block; a device that names no real gear must never match.
        intent = {"reference_settings": [
            {"device": "Marshall JCM800", "role": "amp", "controls": {"Treble": 65, "Bass": 50}},
        ]}
        rig = build_rig({"query": "jcm800 tone", "intent": intent}, fake_lm, catalog)
        amp_block = next(b for b in rig["signal_chain"] if b["module"] == "AMP")
        self.assertAlmostEqual(amp_block["parameters"]["Treble"], 65, delta=0.01)
        self.assertAlmostEqual(amp_block["parameters"]["Bass"], 50, delta=0.01)
        self.assertTrue(any("Marshall JCM800" in note for note in rig["effect_review"]))

    def test_reference_settings_survive_the_conservative_delay_ceiling(self):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        echo = next(e for e in catalog.effects_for_modules(["DLY"])["DLY"] if e["name"] == "Sweet Echo")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Sourced Delay", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "DLY", "fxid": echo["fxid"], "enabled": True, "purpose": "delay", "parameters": {"F.Back": 10}},
            ]}

        # 85% feedback is well above _review_effect_sympathy's generic 45%
        # ceiling for unsourced delay guesses; a confirmed match to the real
        # pedal (Boss DM-2) must not get pulled back down to that default.
        intent = {
            "effects": [{"name": "Delay", "purpose": "slapback"}],
            "reference_settings": [{"device": "Boss DM-2", "role": "effect", "controls": {"Feedback": 85}}],
        }
        rig = build_rig({"query": "dm-2 slapback", "intent": intent}, fake_lm, catalog)
        delay_block = next(b for b in rig["signal_chain"] if b["module"] == "DLY")
        self.assertAlmostEqual(delay_block["parameters"]["F.Back"], 85, delta=0.01)

    def test_reference_settings_for_a_different_amp_are_not_applied(self):
        catalog = GP50Catalog()
        jcm800 = next(e for e in catalog.effects_for_modules(["AMP"])["AMP"] if e["name"] == "UK 800")
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Unmatched", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": jcm800["fxid"], "enabled": True, "purpose": "amp", "parameters": {"Treble": 10}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        # Copying a Fender's dial numbers onto a Marshall model is exactly the
        # cross-tonestack mistake this feature must not make.
        intent = {"reference_settings": [
            {"device": "Fender Twin Reverb", "role": "amp", "controls": {"Treble": 90}},
        ]}
        rig = build_rig({"query": "jcm800 tone", "intent": intent}, fake_lm, catalog)
        amp_block = next(b for b in rig["signal_chain"] if b["module"] == "AMP")
        self.assertEqual(amp_block["parameters"]["Treble"], 10)


class TargetToneTests(unittest.TestCase):
    """The target-tone-vector path (score_effect_relevance's `target_tone`,
    wired through `_target_tone_from_intent`/`_best_amp`/`compact_for_prompt`)
    lets a purely descriptive request rank candidates by sonic similarity
    even when none of its words literally appear in the catalogue."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = GP50Catalog()

    def test_character_words_build_a_partial_tone_vector(self):
        tone = _target_tone_from_intent({"query": "", "intent": {"character": ["warm", "dark"], "gain": "medium"}})
        self.assertGreater(tone["warmth"], 0.5)
        self.assertLess(tone["brightness"], 0.5)
        self.assertAlmostEqual(tone["gain"], 0.5)

    def test_tone_only_query_favours_a_warm_dark_amp_over_a_bright_clean_one(self):
        amps = self.catalog.effects_for_modules(["AMP"])["AMP"]
        tweedy = next(e for e in amps if e["name"] == "Tweedy")  # warm, low headroom, dark-ish breakup
        twin = next(e for e in amps if e["name"] == "Dark Twin")  # scooped mids, sparkling highs, high headroom
        target_tone = _target_tone_from_intent({"query": "", "intent": {"character": ["warm", "dark", "gritty"]}})
        tweedy_score = score_effect_relevance(tweedy, set(), target_tone=target_tone)
        twin_score = score_effect_relevance(twin, set(), target_tone=target_tone)
        self.assertGreater(tweedy_score, twin_score)

    def test_tone_only_query_favours_high_gain_amp_for_metal_words(self):
        amps = self.catalog.effects_for_modules(["AMP"])["AMP"]
        ev51 = next(e for e in amps if e["name"] == "EV 51")  # Peavey 5150-style high gain
        jc120 = next(e for e in amps if e["name"] == "J-120 CL")  # ultra-clean solid state
        target_tone = _target_tone_from_intent({"query": "", "intent": {"character": ["tight", "saturated", "aggressive"]}})
        self.assertGreater(
            score_effect_relevance(ev51, set(), target_tone=target_tone),
            score_effect_relevance(jc120, set(), target_tone=target_tone),
        )

    def test_best_amp_falls_back_to_tone_similarity_with_no_keyword_overlap(self):
        amps = self.catalog.effects_for_modules(["AMP"])["AMP"]
        target_tone = _target_tone_from_intent({"query": "", "intent": {"character": ["warm", "dark"], "gain": "low"}})
        choice = _best_amp(amps, set(), target_tone)
        self.assertIn(choice["musical_profile"].get("family"), {"american", "boutique"})
        self.assertNotIn(choice["musical_profile"].get("family"), {"modern_high_gain"})

    def test_compact_for_prompt_narrows_by_tone_alone_when_terms_are_empty(self):
        target_tone = _target_tone_from_intent({"query": "", "intent": {"character": ["aggressive", "tight", "saturated"]}})
        narrowed = self.catalog.compact_for_prompt(["AMP"], terms=set(), target_tone=target_tone, limit_per_module=5)
        names = {e["name"] for e in narrowed["modules"]["AMP"]}
        self.assertIn("EV 51", names)
        self.assertNotIn("J-120 CL", names)


class CabAffinityTests(unittest.TestCase):
    """CAB `musical_profile.pairs_well_with` gives `_matching_cab` (and the
    LLM, via compact_for_prompt) an explicit amp/cab affinity signal for
    cases where a cab's name/origin doesn't obviously overlap the amp's."""

    def test_matching_cab_uses_amp_family_when_name_and_origin_both_miss(self):
        amp = {"name": "Totally Unrelated Amp", "origin": "Nobody's Amp Co", "musical_profile": {"family": "modern_high_gain"}}
        cabs = [
            {"name": "TWD CP 1x8", "origin": "Fender Champ 1x8", "musical_profile": {"pairs_well_with": ["american"]}},
            {"name": "Bog 4x12", "origin": "Bogner 4x12", "musical_profile": {"pairs_well_with": ["modern_high_gain", "bogner"]}},
        ]
        self.assertEqual(_matching_cab(amp, cabs)["name"], "Bog 4x12")

    def test_real_catalogue_cabs_all_declare_an_amp_family_affinity(self):
        catalog = GP50Catalog()
        cabs = catalog.effects_for_modules(["CAB"])["CAB"]
        for cab in cabs:
            if cab["type"] == "User IR":
                continue
            pairs = (cab.get("musical_profile") or {}).get("pairs_well_with")
            self.assertTrue(pairs, f"{cab['name']} has no pairs_well_with data")


class SignalChainOrderingTests(unittest.TestCase):
    """The returned signal_chain (what the UI renders block-by-block, and
    the "Advanced: edit preset data" JSON) must reflect real GP-50 hardware
    order (NR->PRE->DST->AMP->CAB->EQ->MOD->DLY->RVB), regardless of the
    order the model happened to emit blocks in — the binary preset itself is
    unaffected either way since create_preset writes each block by its own
    fixed module_id offset, not list position."""

    def test_signal_chain_is_returned_in_hardware_order_not_emission_order(self):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        comp = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["type"] == "Comp")

        def fake_lm(system, user, schema, **kwargs):
            # Deliberately emit AMP/CAB before the PRE compressor, mirroring
            # the model's natural tendency to lead with the amp/cab choice.
            return {"preset_name": "Order", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "PRE", "fxid": comp["fxid"], "enabled": True, "purpose": "comp", "parameters": {}},
            ]}

        rig = build_rig({"query": "compressed clean tone", "intent": {"effects": [{"name": "Comp", "purpose": "even out picking", "starting_point": "moderate"}]}}, fake_lm, catalog)
        modules = [block["module"] for block in rig["signal_chain"]]
        self.assertEqual(modules, ["PRE", "AMP", "CAB"])

    def test_reverb_module_is_always_offered_even_when_intent_never_names_it(self):
        catalog = GP50Catalog()

        def fake_lm(system, user, schema, **kwargs):
            enum = schema["properties"]["signal_chain"]["items"]["properties"]["fxid"]["enum"]
            self.assertTrue(
                set(enum) & {e["fxid"] for e in catalog.effects_for_modules(["RVB"])["RVB"]},
                "no RVB fxid was offered even though RVB should always be in the schema enum",
            )
            amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
            cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
            return {"preset_name": "NoSpace", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        # An intent that names no effects at all (nothing hints at reverb) —
        # RVB must still be schema-offered so the model isn't structurally
        # prevented from adding a touch of it when musically appropriate.
        build_rig({"query": "tight metal rhythm", "intent": {"effects": []}}, fake_lm, catalog)


if __name__ == "__main__": unittest.main()
