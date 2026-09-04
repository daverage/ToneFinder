import unittest

from gp50.catalog import GP50Catalog, score_effect_relevance
from gp50.rig_builder import (
    SYSTEM,
    _best_amp,
    _fallback_module_for_effect,
    _matching_cab,
    _relevant_modules,
    _target_tone_from_intent,
    build_rig,
    importance_weight,
)


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
        # web_research_notes is live-fetched external text (see SYSTEM's own
        # disclaimer); the whole request/catalogue/intent blob must sit
        # inside an explicit <data> boundary so the model has a textual line
        # between "what I'm told to do" and "what I'm told about". (A retry
        # addendum, if the model's first plan was rejected, is appended
        # after </data> — see build_rig's `retry` handling — so check
        # presence of both tags rather than the whole prompt's suffix.)
        self.assertIn("<data>\n", captured["prompt"])
        self.assertIn("\n</data>", captured["prompt"])
        self.assertIn("not instructions", SYSTEM)
        self.assertIn("web_research_notes", SYSTEM)

    def test_a_validated_first_attempt_still_falls_back_to_the_query_when_preset_name_is_blank(self):
        # Real observed bug: unlike _salvage_valid_blocks/_builtin_fallback
        # (both already fall back to the user's own query text), this main
        # path used to go straight from "model omitted preset_name" to
        # _safe_preset_name's bare hardcoded "GP-50 Tone" default whenever
        # the model's plan otherwise validated fine on the first attempt —
        # so every such preset got the same generic name regardless of the
        # actual request, rather than needing salvage to ever kick in.
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        rig = build_rig({"query": "warm bluesy lead"}, fake_lm, catalog)
        self.assertNotEqual(rig["preset_name"], "GP-50 Tone")
        self.assertEqual(rig["preset_name"], "warm blues")

    def test_system_prompt_disambiguates_catalogue_legend_shape_from_output_shape(self):
        # Real observed confusion (gemma-4-12B-it-4bit): with structured
        # output not fully enforced on this large a schema, the model
        # returned signal_chain items shaped like the catalogue's own
        # {"id","name","type","origin","profile"} reference entries instead
        # of the requested {"module","fxid","enabled","purpose","parameters"}
        # block shape, and consistently omitted "fxid" entirely. This is a
        # defense-in-depth prompt fix (gp50.rig_builder._resolve_by_exact_name
        # is the deterministic backstop for when it still happens).
        self.assertIn('"id", "name", "type", "origin", "profile"', SYSTEM)
        self.assertIn('"module", "fxid", "enabled", "purpose", "parameters"', SYSTEM)
        self.assertIn("never omit", SYSTEM.lower())

    def test_importance_weight_maps_tiers_and_defaults_unknown_to_supporting(self):
        self.assertEqual(importance_weight("essential"), 1.0)
        self.assertEqual(importance_weight("optional"), 0.25)
        self.assertEqual(importance_weight("not-a-tier"), 0.5)

    def test_relevant_modules_finds_pre_for_a_talk_box_request_via_descriptor_fallback(self):
        # Real observed failure: a local model named the effect "TalkBox"
        # with no wah/filter/envelope keyword anywhere in its text, and
        # didn't flag hardware_available=false either (tone_finder.py's
        # mechanism for that is LLM-dependent and doesn't always fire) — so
        # PRE was never offered in build_rig's schema and the built preset
        # silently had no PRE block at all despite the user explicitly
        # asking for a wah-like effect. _fallback_module_for_effect (via
        # descriptor_relevance) is the deterministic backstop for exactly
        # this: it must find PRE (C-Wah/Toucher/Crier, all vocal-character
        # PRE effects) from the effect's own text alone.
        catalog = GP50Catalog()
        payload = {"intent": {"gain": "high", "effects": [
            {"name": "TalkBox", "purpose": "Achieve iconic vocal-like guitar sound", "starting_point": "Heil Sound or MXR"},
            {"name": "Overdrive", "purpose": "Warm, singing lead tone drive", "starting_point": "Boss OD-1"},
        ]}}
        modules = _relevant_modules(payload, catalog)
        self.assertIn("PRE", modules)

    def test_relevant_modules_offers_the_fallback_module_even_when_something_else_already_matched(self):
        # The same request's starting_point text ("Heil Sound or MXR") also
        # substring-matches MOD via "mxr" (an unrelated MXR Phase 90-style
        # phaser's own catalogue keyword) — a real false positive in the
        # coarse per-module keyword aggregation, confirmed against this
        # catalogue. That spurious match must not crowd out the fallback's
        # correct PRE answer: both should end up offered.
        catalog = GP50Catalog()
        payload = {"intent": {"effects": [
            {"name": "TalkBox", "purpose": "Achieve iconic vocal-like guitar sound", "starting_point": "Heil Sound or MXR"},
        ]}}
        modules = _relevant_modules(payload, catalog)
        self.assertIn("MOD", modules)  # the spurious "mxr" substring match, unavoidable at this layer
        self.assertIn("PRE", modules)  # the fallback's actually-correct answer, still present alongside it

    def test_relevant_modules_stays_unpolluted_for_an_ordinary_well_matched_request(self):
        # The fallback runs for every effect, not just unmatched ones (see
        # _relevant_modules' docstring) — make sure that doesn't add noise
        # to a request that already matches cleanly.
        catalog = GP50Catalog()
        payload = {"intent": {"effects": [
            {"name": "Delay", "purpose": "short slapback echo", "starting_point": "Mix 15%"},
        ]}}
        self.assertEqual(_relevant_modules(payload, catalog), ["AMP", "CAB", "RVB", "DLY"])

    def test_fallback_module_for_effect_returns_none_for_generic_text(self):
        catalog = GP50Catalog()
        self.assertIsNone(_fallback_module_for_effect("a nice sound for the song", catalog))
        self.assertIsNone(_fallback_module_for_effect("", catalog))

    def test_prompt_tells_the_model_which_modules_share_one_slot(self):
        # A request whose intent names two PRE-flavored effects (compressor
        # and wah) both compete for the GP-50's single PRE slot — the model
        # needs this spelled out or it may not realize only one can survive
        # validation. See gp50.catalog.GP50Catalog.shared_effect_slots and
        # gp50.rig_builder._slot_sharing_guidance.
        catalog = GP50Catalog()
        captured = {}

        def fake_lm(system, user, schema, **kwargs):
            captured["prompt"] = user
            amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
            cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
            return {"preset_name": "Test", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        build_rig({"query": "funk rhythm"}, fake_lm, catalog)
        self.assertIn("one hardware slot per module", captured["prompt"])
        self.assertIn("PRE (one slot, choose from:", captured["prompt"])
        self.assertIn("Wah", captured["prompt"])

    def test_multiple_pre_candidates_are_resolved_by_relevance_not_order(self):
        # PRE holds one of Comp/Boost/Filter/Pitch/Sim/Wah at a time (see
        # GP50Catalog.shared_effect_slots). A tone can legitimately call for
        # several of those roles at once — a wah, an octave texture, a solo
        # boost — but the hardware only has room for one. `_keep_best_per_
        # module` (wired into build_rig before validate_rig) resolves that
        # by scoring each candidate's own purpose text against the request,
        # the same mechanism `_resolve_missing_fxid` etc. already use — not
        # by which one the model happened to list first (here, deliberately,
        # the weakest match — Boost — is listed first, to prove order isn't
        # what decides it).
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        wah = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "C-Wah")
        octa = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "OCTA")
        boost = next(e for e in catalog.effects_for_modules(["PRE"])["PRE"] if e["name"] == "B-Boost")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Funk", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "PRE", "fxid": boost["fxid"], "enabled": True, "purpose": "slight solo lift", "parameters": {}},
                {"module": "PRE", "fxid": octa["fxid"], "enabled": True, "purpose": "octave-up texture on the outro", "parameters": {}},
                {"module": "PRE", "fxid": wah["fxid"], "enabled": True, "purpose": "classic funk rhythm wah cry baby sweep", "parameters": {}},
            ]}

        rig = build_rig({"query": "funk rhythm wah guitar"}, fake_lm, catalog)
        pre_blocks = [b for b in rig["signal_chain"] if b["module"] == "PRE"]
        self.assertEqual(len(pre_blocks), 1)
        self.assertEqual(pre_blocks[0]["effect_name"], "C-Wah")

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
        # RVB is included even though the model's plan never named it: a real
        # amp tone is rarely fully dry, so `_ensure_reverb` fills that gap
        # with a conservative default touch of it (see test_rig_builder's
        # ReverbDefaultTests below for that behavior in isolation).
        self.assertEqual({block["module"] for block in rig["signal_chain"]}, {"AMP", "CAB", "RVB"})

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
        # (PRE before AMP/CAB, RVB last — see MODULE_ORDER), not emission
        # order. RVB itself was never in the model's plan; `_ensure_reverb`
        # adds a conservative default touch of it.
        self.assertEqual([block["module"] for block in rig["signal_chain"]], ["PRE", "AMP", "CAB", "RVB"])
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
        self.assertEqual({block["module"] for block in rig["signal_chain"]}, {"AMP", "CAB", "RVB"})
        self.assertLessEqual(len(rig["preset_name"].encode("latin-1")), 10)
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
        # Resolved by scoring each purpose against the catalogue's own
        # musical_profile data (see _resolve_missing_fxid), not a hardcoded
        # keyword->model table — so "light overdrive grit" lands on Yellow OD
        # (catalogue role "light_drive") rather than an arbitrary fixed OD
        # pick, and "large plate reverb" lands on "Plate L" (the catalogue's
        # own bigger plate) rather than plain "Plate".
        self.assertTrue({"COMP", "Yellow OD", "Pure", "Plate L"}.issubset(names))

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
        # 28, not the old 35: the ceiling must equal the intended "tightener,
        # not saturator" value directly (see test_drive_gain_and_fuzz_land_
        # on_the_intended_tightener_value_not_a_looser_ceiling below, which
        # reproduces the exact regression this fixed).
        self.assertLessEqual(blocks["DST"]["parameters"]["Gain"], 28)
        self.assertEqual(blocks["NR"]["parameters"]["THRE"], 20)

    def test_drive_gain_and_fuzz_land_on_the_intended_tightener_value_not_a_looser_ceiling(self):
        # Real observed bug, the same class as RVB Decay/DLY Time/Feedback:
        # every DST model's own catalogue Gain default is >=40 and every Fuzz
        # default is 50 -- both above the old 35/40 ceilings (so validate_rig's
        # catalogue-default back-fill always slipped through unreduced to the
        # ceiling, not down to the intended 28/32 "tightener" value).
        catalog = GP50Catalog()
        amp = next(e for e in catalog.effects_for_modules(["AMP"])["AMP"] if e["name"] == "UK 800")
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        fuzz = next(e for e in catalog.effects_for_modules(["DST"])["DST"] if e["name"] == "Red Haze")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Fuzz Test", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "DST", "fxid": fuzz["fxid"], "enabled": True, "purpose": "fuzz", "parameters": {}},
            ]}

        rig = build_rig({"query": "high gain fuzz", "intent": {"gain": "high"}}, fake_lm, catalog)
        dst = next(b for b in rig["signal_chain"] if b["module"] == "DST")
        self.assertEqual(dst["parameters"]["Fuzz"], 32.0)

    def test_a_pre_existing_gate_with_unset_threshold_is_still_pulled_down_to_20(self):
        # Distinct from test_high_gain_plan_is_capped_and_gets_a_noise_gate:
        # that test has no NR block at all, so _manage_gain inserts a fresh
        # one with THRE explicitly set to 20 already -- always correct, even
        # before this fix. This reproduces the actual regression: the model
        # already proposed an NR/Gate block itself, leaving THRE unset (so
        # validate_rig back-fills the catalogue's own default, 50) -- the old
        # 45 ceiling let that slip through at 45, more than double the
        # intended 20 and directly against this block's own "without
        # choking sustained notes" purpose.
        catalog = GP50Catalog()
        amp = next(e for e in catalog.effects_for_modules(["AMP"])["AMP"] if e["name"] == "UK 800")
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        gate = next(e for e in catalog.effects_for_modules(["NR"])["NR"] if e["type"] == "Gate")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Gate Test", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "NR", "fxid": gate["fxid"], "enabled": True, "purpose": "gate", "parameters": {}},
            ]}

        rig = build_rig({"query": "high gain", "intent": {"gain": "high"}}, fake_lm, catalog)
        nr = next(b for b in rig["signal_chain"] if b["module"] == "NR")
        self.assertEqual(nr["parameters"]["THRE"], 20.0)

    def test_analog_delay_feedback_lands_on_the_intended_value_not_a_looser_ceiling(self):
        # "Analog" is the one DLY model using the literal "Feedback" key
        # (every other model uses "F.Back") -- its catalogue default is 50,
        # above the old flat 45 ceiling (which also didn't distinguish
        # long/short requests the way the intended default already did).
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        analog = next(e for e in catalog.effects_for_modules(["DLY"])["DLY"] if e["name"] == "Analog")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Analog DLY", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "DLY", "fxid": analog["fxid"], "enabled": True, "purpose": "delay", "parameters": {}},
            ]}

        rig = build_rig({"query": "short slapback delay", "intent": {"effects": [{"name": "Delay", "purpose": "short slapback"}]}}, fake_lm, catalog)
        dly = next(b for b in rig["signal_chain"] if b["module"] == "DLY")
        self.assertEqual(dly["parameters"]["Feedback"], 22.0)

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
        # 42, not the old 55: the ceiling must actually equal the intended
        # conservative value, since validate_rig always back-fills a missing
        # parameter from the catalogue's own default first — a looser
        # ceiling above that default (55 vs. RVB's own 50) never engages at
        # all (see test_default_touch_of_reverb_pulls_decay_below_the_
        # catalogue_default below, which reproduces that exact regression).
        self.assertLessEqual(blocks["RVB"]["parameters"]["Decay"], 42)
        self.assertLessEqual(blocks["DLY"]["parameters"]["Time"], 420)
        self.assertTrue(rig["effect_review"])

    def test_default_touch_of_reverb_pulls_decay_below_the_catalogue_default(self):
        # Real observed bug: RVB's own catalogue Decay default is 50, and the
        # old ceiling here was 55 -- so an untouched Decay silently passed
        # through at 50 on every default-touch-of-reverb rig (the common
        # case: the model didn't ask for reverb at all, _ensure_reverb added
        # one), well short of the "kept subtle" claim in the UI. Decay must
        # actually be pulled down, the same way Mix already reliably is.
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "NoRVB", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        rig = build_rig({"query": "clean tone"}, fake_lm, catalog)
        rvb = next(b for b in rig["signal_chain"] if b["module"] == "RVB")
        catalogue_default = next(p["default"] for p in catalog.get(rvb["fxid"])["params"] if p["name"] == "Decay")
        self.assertEqual(catalogue_default, 50.0)
        self.assertLess(rvb["parameters"]["Decay"], catalogue_default)
        self.assertLessEqual(rvb["parameters"]["Decay"], 30)

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
        # RVB trails, both in default hardware order and because
        # `_ensure_reverb` appends its default touch of reverb last.
        self.assertEqual(modules, ["PRE", "AMP", "CAB", "RVB"])

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


class DefaultReverbTests(unittest.TestCase):
    """`_ensure_reverb` fills the gap left by the model's own conservative
    interpretation step: a real amp tone is rarely fully dry, but a request
    that never literally says "reverb" routinely produces a plan with no RVB
    block even though RVB is always schema-offered."""

    def _amp_cab_only_rig(self, query: str, intent: dict | None = None):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Test", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
            ]}

        payload = {"query": query}
        if intent is not None:
            payload["intent"] = intent
        return build_rig(payload, fake_lm, catalog), catalog

    def test_a_default_reverb_is_added_when_the_model_omits_one(self):
        rig, catalog = self._amp_cab_only_rig("warm clean tone")
        reverb = next((b for b in rig["signal_chain"] if b["module"] == "RVB"), None)
        self.assertIsNotNone(reverb)
        self.assertTrue(reverb["enabled"])
        # Kept subtle, same ceiling `_review_effect_sympathy` gives any RVB block.
        self.assertLessEqual(reverb["parameters"]["Mix"], 28)
        self.assertTrue(any("added by default" in note for note in rig["effect_review"]))

    def test_genre_cues_pick_a_matching_reverb_type(self):
        rig, _ = self._amp_cab_only_rig("surf rock vintage clean tone")
        reverb = next(b for b in rig["signal_chain"] if b["module"] == "RVB")
        self.assertEqual(reverb["effect_name"], "Spring")

    def test_no_reverb_is_added_when_the_request_is_explicitly_dry(self):
        rig, _ = self._amp_cab_only_rig("tight, completely dry funk rhythm tone")
        self.assertNotIn("RVB", {b["module"] for b in rig["signal_chain"]})

    def test_a_model_chosen_reverb_is_not_overridden_or_duplicated(self):
        catalog = GP50Catalog()
        amp = catalog.effects_for_modules(["AMP"])["AMP"][0]
        cab = catalog.effects_for_modules(["CAB"])["CAB"][0]
        hall = next(e for e in catalog.effects_for_modules(["RVB"])["RVB"] if e["name"] == "Hall")

        def fake_lm(system, user, schema, **kwargs):
            return {"preset_name": "Test", "summary": "test", "signal_chain": [
                {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "amp", "parameters": {}},
                {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "cab", "parameters": {}},
                {"module": "RVB", "fxid": hall["fxid"], "enabled": True, "purpose": "space", "parameters": {}},
            ]}

        rig = build_rig({"query": "ambient wash"}, fake_lm, catalog)
        reverbs = [b for b in rig["signal_chain"] if b["module"] == "RVB"]
        self.assertEqual(len(reverbs), 1)
        self.assertEqual(reverbs[0]["effect_name"], "Hall")
        self.assertFalse(any("added by default" in note for note in rig["effect_review"]))


if __name__ == "__main__": unittest.main()
