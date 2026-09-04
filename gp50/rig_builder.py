"""LLM-facing musical planning, deliberately separated from binary serialization."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Callable

from .catalog import GP50Catalog
from .catalog import canonical_module
from .catalog import default_catalog
from .catalog import score_effect_relevance as _score_effect
from .validator import MODULE_ORDER, RigValidationError, find_parameter, validate_rig, _coerce_parameter

RIG_SCHEMA: dict[str, Any] = {
    "type": "object", "properties": {
        "preset_name": {"type": "string", "maxLength": 10},
        "summary": {"type": "string"},
        "signal_chain": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
            "module": {"type": "string"}, "fxid": {"type": "integer"}, "enabled": {"type": "boolean"},
            "purpose": {"type": "string"}, "parameters": {"type": "object", "additionalProperties": {"type": "number"}},
        }, "required": ["module", "fxid", "enabled", "purpose", "parameters"], "additionalProperties": False}},
    }, "required": ["preset_name", "summary", "signal_chain"], "additionalProperties": False,
}

SYSTEM = """You are a guitar effects expert building a Valeton GP-50 rig.
Choose only from the supplied GP-50 catalogue. It is authoritative: never invent a model, fxid, module, parameter, or range. Every catalogue entry includes a functional description: use it, the model's type, origin, and controls to make a musical choice—not merely the model name. Use at most one model per module and keep the preset short and practical. Return JSON only."""


def _safe_preset_name(value: Any) -> str:
    """Fit a name into what Valeton Suite's own preset browser actually
    displays, which is shorter than the GP-50's 16-byte binary name field.

    The field itself holds up to 15 usable characters + a NUL terminator
    (`gp50/preset.py:NAME_TEXT_SIZE`) and the device's own screen shows a
    full 15-character name correctly. But every real Suite-authored name
    this project has seen tops out at 10 characters, and Suite's own
    re-export of a this-project-generated 15-character name
    ("The lead guitar") silently truncated it to 10 ("The lead g") on save —
    while the original 15-character version showed blank in Suite's preset
    list despite displaying correctly on the device (live report, 2026-09).
    Capping here at Suite's real limit, not the hardware's, is what actually
    avoids the blank-name bug end to end.
    """
    name = str(value or "GP-50 Tone").strip() or "GP-50 Tone"
    encoded = name.encode("latin-1", errors="replace")[:10]
    return encoded.decode("latin-1", errors="replace").rstrip() or "GP-50 Tone"


def _resolve_missing_fxid(raw: dict[str, Any], catalog: GP50Catalog) -> dict[str, Any] | None:
    """Map an effect role to one unambiguous GP-50 catalogue choice.

    Local models sometimes supply a correct module and musical purpose but
    omit the numeric `fxid`. Rather than a hand-maintained table of English
    needles to specific model names (which has to be extended in Python
    every time a new phrasing or a new catalogue model shows up, and can't
    take advantage of a real, specific AI suggestion beyond whatever needles
    someone thought to hardcode), this scores every candidate in the target
    module the same way `_best_amp`/`_matching_cab`/`_pick_reverb` already
    do: against the catalogue's own `musical_profile` (keywords/character/
    best_for/roles) via `score_effect_relevance`. That data is the single
    source of truth (see CLAUDE.md), so a block whose `purpose` genuinely
    matches a model's documented character finds it without any code change
    here — an unfamiliar or too-generic description simply scores 0 and is
    left unresolved rather than guessed.
    """
    module = canonical_module(raw.get("module"))
    if module not in {"PRE", "DST", "EQ", "MOD", "DLY", "RVB"}:
        return None
    effects = catalog.effects_for_modules([module]).get(module, [])
    if not effects:
        return None
    description = " ".join(str(raw.get(key, "")) for key in ("effect_name", "name", "purpose", "summary"))
    terms = _tokenize(description)
    if terms:
        best = max(effects, key=lambda e: _score_effect(e, terms))
        if _score_effect(best, terms) > 0:
            return best
    # Exact catalogue names remain safe even if the model calls the field
    # `name` instead of the schema's required numeric `fxid`.
    return next((effect for effect in effects if effect["name"].lower() in description.lower()), None)


def _score_raw_block(raw: dict[str, Any], effect: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog) -> float:
    """How well one already-resolved signal_chain block actually fits this
    request — used only to break a tie when two blocks want the same
    physical module (see `_keep_best_per_module`). Combines the block's own
    stated purpose/name with the overall request's terms/target_tone, scored
    the same way `_best_amp`/`_matching_cab`/`_pick_reverb`/
    `_resolve_missing_fxid` already score a request against the catalogue's
    own `musical_profile` data.
    """
    text = " ".join(str(raw.get(key, "")) for key in ("purpose", "effect_name", "name"))
    terms = _fallback_terms(payload) | _tokenize(text)
    return _score_effect(effect, terms, target_tone=_target_tone_from_intent(payload))


def _keep_best_per_module(blocks: list[Any], payload: dict[str, Any], catalog: GP50Catalog) -> list[Any]:
    """Keep, per physical GP-50 module, whichever candidate block actually
    matches this request best — not just whichever the model happened to
    list first.

    Several modules bundle genuinely distinct effect roles onto one shared
    hardware slot (`GP50Catalog.shared_effect_slots`: PRE holds one of
    Comp/Boost/Filter/Pitch/Sim/Wah, not one of each — a tone that
    legitimately calls for a wah *and* an octave *and* a boost still only
    gets one). Rather than a fixed priority order between those roles (which
    would just be a different kind of hardcoded guess — a wah matters more
    than a boost for a funk tone, but a boost might matter more than a wah
    for a solo-lift request), this asks the same relevance scoring
    `_resolve_missing_fxid` and the amp/cab/reverb pickers already use to
    judge which specific candidate best fits *this* request, and keeps that
    one. A block whose `fxid`/`module` can't be resolved yet is left
    untouched (not deduped) so `validate_rig` still reports its real problem
    instead of it being silently dropped here.
    """
    best: dict[str, tuple[float, int]] = {}
    kept: list[Any] = []
    for block in blocks:
        effect = catalog.get(block.get("fxid")) if isinstance(block, dict) else None
        if effect is None:
            kept.append(block)
            continue
        module = canonical_module(block.get("module"))
        score = _score_raw_block(block, effect, payload, catalog)
        if module in best:
            prev_score, prev_index = best[module]
            if score <= prev_score:
                continue
            kept[prev_index] = block
            best[module] = (score, prev_index)
        else:
            best[module] = (score, len(kept))
            kept.append(block)
    return kept


def _salvage_valid_blocks(result: dict[str, Any], catalog: GP50Catalog, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Keep only unambiguously repairable choices after two failed LLM attempts.

    Valid fxids are retained. Missing fxids can be repaired only from a known
    GP-50 module plus a canonical musical role (for example, plate reverb).
    """
    blocks = []
    for raw in result.get("signal_chain", []) if isinstance(result, dict) else []:
        if not isinstance(raw, dict):
            continue
        effect = catalog.get(raw.get("fxid")) or _resolve_missing_fxid(raw, catalog)
        # A block that omits `parameters` entirely is just as usable here as
        # one with an empty object — default it instead of requiring it, but
        # still reject anything present-and-not-a-dict.
        parameters = raw.get("parameters", {})
        if effect is None or not isinstance(parameters, dict):
            continue
        blocks.append({"module": effect["module"], "fxid": effect["fxid"], "enabled": bool(raw.get("enabled", True)),
                       "purpose": raw.get("purpose", "AI-selected supporting effect"), "parameters": parameters})
    # A GP-50 has one physical slot per module (see
    # GP50Catalog.shared_effect_slots); when two salvaged blocks want the
    # same one, keep whichever actually fits this request best rather than
    # just the model's emission order.
    blocks = _keep_best_per_module(blocks, payload or {}, catalog)
    if not blocks:
        return None
    # A model whose plan needed salvaging in the first place often also
    # omitted (or mangled) `preset_name` itself — falling back straight to a
    # fixed generic string ("GP-50 Tone" for every such rig) is a worse
    # default than the user's own request text, which `_builtin_fallback`
    # already uses in exactly this situation.
    plan = {"preset_name": _safe_preset_name(result.get("preset_name") or (payload or {}).get("query")),
            "summary": result.get("summary", "Catalogue-corrected AI rig"), "signal_chain": blocks}
    try:
        rig = validate_rig(plan, catalog)
        rig["validation_warning"] = "The local model omitted some GP-50 IDs; known effect roles were translated to catalogue effects."
        return rig
    except RigValidationError:
        # Descriptive parameter text (such as “high feedback”) is useful to the
        # user but is not a hardware value. Retain the resolved effects at their
        # documented defaults rather than rejecting the whole preset.
        for block in blocks:
            block["parameters"] = {}
        try:
            rig = validate_rig(plan, catalog)
            rig["validation_warning"] = "Known effect roles were translated to GP-50 effects; their parameters use safe defaults for adjustment."
            return rig
        except RigValidationError:
            return None


_FALLBACK_STOPWORDS = {"amp", "amplifier", "tone", "sound", "guitar", "style", "type", "the", "and", "for", "with"}


def _fallback_terms(payload: dict[str, Any]) -> set[str]:
    intent = payload.get("intent", {})
    text = " ".join([
        str(payload.get("query", "")),
        *map(str, intent.get("amp_families", []) if isinstance(intent, dict) else []),
        *map(str, intent.get("character", []) if isinstance(intent, dict) else []),
    ])
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _FALLBACK_STOPWORDS}


# Deliberately small, hand-curated adjective -> normalized-tone-dimension
# lexicon (see each catalogue entry's own `tone` object for the dimension
# names). This is what lets a request built entirely from descriptive words
# ("warm", "scooped", "tight") narrow AMP/CAB/DLY/RVB candidates by sonic
# similarity even when none of those words literally appear in a catalogue
# entry's name/origin/keywords — deterministic, no embeddings.
_TONE_WORDS: dict[str, dict[str, float]] = {
    "bright": {"brightness": 0.85}, "brighter": {"brightness": 0.85},
    "chimey": {"brightness": 0.8}, "chime": {"brightness": 0.8},
    "sparkly": {"brightness": 0.85}, "sparkling": {"brightness": 0.85}, "trebly": {"brightness": 0.85},
    "dark": {"brightness": 0.15, "warmth": 0.75}, "darker": {"brightness": 0.15, "warmth": 0.75},
    "warm": {"warmth": 0.8, "brightness": 0.35}, "muddy": {"brightness": 0.2, "low_end": 0.75},
    "tight": {"tightness": 0.85}, "tighter": {"tightness": 0.85}, "loose": {"tightness": 0.2},
    "aggressive": {"saturation": 0.8, "gain": 0.75, "aggression": 0.8},
    "gritty": {"saturation": 0.7, "gain": 0.6}, "grit": {"saturation": 0.65, "gain": 0.55},
    "heavy": {"gain": 0.8, "saturation": 0.75, "low_end": 0.65},
    "clean": {"gain": 0.1, "saturation": 0.05}, "pristine": {"gain": 0.05, "saturation": 0.02, "brightness": 0.65},
    "scooped": {"mid_focus": 0.15}, "compressed": {"compression": 0.8}, "squashed": {"compression": 0.85},
    "dynamic": {"compression": 0.2}, "open": {"compression": 0.25, "brightness": 0.6},
    "saturated": {"saturation": 0.85}, "fuzzy": {"saturation": 0.85, "tightness": 0.25},
    "smooth": {"attack": 0.35, "saturation": 0.5}, "punchy": {"attack": 0.75, "low_end": 0.6},
    "percussive": {"attack": 0.8}, "crunchy": {"gain": 0.55, "saturation": 0.55},
    "metal": {"gain": 0.85, "saturation": 0.8, "tightness": 0.75},
    "vintage": {"saturation": 0.4, "brightness": 0.45}, "modern": {"tightness": 0.7},
    "slapback": {"warmth": 0.6, "modulation": 0.05}, "spacious": {"size": 0.8},
    "ambient": {"size": 0.8, "warmth": 0.5}, "wash": {"size": 0.9, "density": 0.7},
    "shimmer": {"brightness": 0.65, "size": 0.6}, "airy": {"brightness": 0.65, "warmth": 0.35},
}
_TONE_PHRASES: dict[str, dict[str, float]] = {
    "mid forward": {"mid_focus": 0.85}, "mid-forward": {"mid_focus": 0.85}, "midforward": {"mid_focus": 0.85},
    "edge of breakup": {"gain": 0.35, "compression": 0.45},
}
_GAIN_WORD_TONE = {"high": 0.8, "medium": 0.5, "low": 0.2, "clean": 0.1}


def _target_tone_from_intent(payload: dict[str, Any]) -> dict[str, float]:
    """Build a partial normalized tone vector from free-text intent
    (`intent.character`, `intent.gain`) plus the raw query, so a request made
    of descriptive words alone still narrows candidates by sonic similarity
    (see `score_effect_relevance`'s `target_tone` argument) rather than
    relying only on literal keyword overlap."""
    intent = payload.get("intent", {}) if isinstance(payload.get("intent"), dict) else {}
    text = " ".join([str(payload.get("query", "")), *map(str, intent.get("character", []) or [])]).lower()
    contributions: dict[str, list[float]] = {}

    def add(hints: dict[str, float]) -> None:
        for dim, value in hints.items():
            contributions.setdefault(dim, []).append(value)

    for phrase, hints in _TONE_PHRASES.items():
        if phrase.replace("-", " ") in text.replace("-", " "):
            add(hints)
    for word in _tokenize(text):
        if word in _TONE_WORDS:
            add(_TONE_WORDS[word])

    gain = str(intent.get("gain", "")).lower()
    for word, value in _GAIN_WORD_TONE.items():
        if word in gain:
            add({"gain": value})
            break

    return {dim: sum(values) / len(values) for dim, values in contributions.items()}


def _best_amp(effects: list[dict[str, Any]], terms: set[str], target_tone: dict[str, float] | None = None) -> dict[str, Any]:
    """Fuzzy-match the requested amp family against the catalogue's own model
    names/types/origins, so any documented GP-50 amp is reachable as a safe
    fallback instead of only the three hand-picked brand pairs this replaced.
    `target_tone` (see `_target_tone_from_intent`) lets a purely descriptive
    request ("warm scooped rock crunch") still rank amps by sonic similarity
    even with zero literal word overlap."""
    best = max(effects, key=lambda e: _score_effect(e, terms, target_tone=target_tone))
    if _score_effect(best, terms, target_tone=target_tone) > 0:
        return best
    return next((e for e in effects if e["name"] == "Tweedy"), effects[0])


def _matching_cab(amp: dict[str, Any], cabs: list[dict[str, Any]]) -> dict[str, Any]:
    """GP-50 amp/cab captures share a family name prefix (e.g. "Foxy 30N" pairs
    with "Foxy 2x12"); fall back to the catalogue's own declared amp/cab
    affinity (`musical_profile.family` vs. a cab's `musical_profile.
    pairs_well_with`), then origin-word overlap, then a safe default."""
    amp_prefix = amp["name"].split()[0].lower()
    prefix_match = next((c for c in cabs if c["name"].lower().startswith(amp_prefix)), None)
    if prefix_match:
        return prefix_match
    amp_family = str((amp.get("musical_profile") or {}).get("family", "")).lower()
    if amp_family:
        family_match = next(
            (c for c in cabs if amp_family in {str(x).lower() for x in (c.get("musical_profile") or {}).get("pairs_well_with", [])}),
            None,
        )
        if family_match:
            return family_match
    amp_words = set(re.findall(r"[a-z0-9]+", amp.get("origin", "").lower()))
    best = max(cabs, key=lambda c: len(amp_words & set(re.findall(r"[a-z0-9]+", (c.get("origin") or "").lower()))))
    if amp_words & set(re.findall(r"[a-z0-9]+", (best.get("origin") or "").lower())):
        return best
    return next((c for c in cabs if c["name"] == "TWD CP 1x8"), cabs[0])


def _builtin_fallback(payload: dict[str, Any], catalog: GP50Catalog) -> dict[str, Any]:
    """Return a safe amp/cab-only rig when a local model ignores fxid values.

    This is intentionally conservative: it uses only documented catalogue IDs,
    avoids guessed parameters, and tells the UI that no AI-generated effects
    could be trusted. It keeps preset export available instead of accepting an
    invalid hardware plan.
    """
    terms = _fallback_terms(payload)
    target_tone = _target_tone_from_intent(payload)
    effects = catalog.effects_for_modules(["AMP", "CAB"])
    amp = _best_amp(effects["AMP"], terms, target_tone)
    cab = _matching_cab(amp, effects["CAB"])
    rig = validate_rig({
        "preset_name": _safe_preset_name(payload.get("query")),
        "summary": "Safe built-in GP-50 starting point; refine amp/cab controls to taste.",
        "signal_chain": [
            {"module": "AMP", "fxid": amp["fxid"], "enabled": True, "purpose": "Built-in amp selected from the requested tone family.", "parameters": {}},
            {"module": "CAB", "fxid": cab["fxid"], "enabled": True, "purpose": "Matching built-in cabinet starting point.", "parameters": {}},
        ],
    }, catalog)
    rig["validation_warning"] = (
        "The local model did not provide valid GP-50 effect IDs. A safe built-in "
        "amp/cab starting point was used; add or adjust effects in the editor."
    )
    return rig


_DRY_REQUEST_PHRASES = ("no reverb", "without reverb", "reverb-free", "skip the reverb", "completely dry", "bone dry", "totally dry")


def _wants_no_reverb(payload: dict[str, Any]) -> bool:
    """A request that explicitly asks to stay dry overrides the default touch of reverb below."""
    intent = payload.get("intent", {}) if isinstance(payload.get("intent"), dict) else {}
    text = " ".join([
        str(payload.get("query", "")),
        *map(str, intent.get("character", []) or []),
    ]).lower()
    if any(phrase in text for phrase in _DRY_REQUEST_PHRASES):
        return True
    # "dry" alone (not part of "dry humor" etc.) describing the requested tone,
    # with nothing else in the request naming reverb, is a real preference too.
    return "dry" in _tokenize(text) and "reverb" not in text


def _pick_reverb(payload: dict[str, Any], catalog: GP50Catalog) -> dict[str, Any] | None:
    """Choose a genre-appropriate reverb model from the catalogue's own
    musical_profile data (keywords/character/best_for), the same
    terms/target_tone scoring `_best_amp`/`_matching_cab` already use — so a
    request naming a style (surf, shoegaze, studio, church/cinematic) lands on
    the matching catalogue reverb, and a request with no such cue falls back
    to Room: the catalogue's own "subtle ambience that doesn't wash out fast
    playing" / "tightening up a dry direct tone" model, i.e. the safest
    least-intrusive default touch of reverb.
    """
    effects = catalog.effects_for_modules(["RVB"]).get("RVB", [])
    if not effects:
        return None
    terms = _fallback_terms(payload)
    target_tone = _target_tone_from_intent(payload)
    best = max(effects, key=lambda e: _score_effect(e, terms, target_tone=target_tone))
    if _score_effect(best, terms, target_tone=target_tone) > 0:
        return best
    return next((e for e in effects if e["name"] == "Room"), effects[0])


def _ensure_reverb(rig: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog) -> tuple[dict[str, Any], str]:
    """Add a conservative, genre-matched reverb block when the model's plan
    has none.

    A real amp/cab tone is almost never fully dry, but `_relevant_modules`'s
    own comment notes the interpretation step is deliberately conservative
    about which effects it lists — so a request that never says "reverb" or
    "space" routinely produces a plan with no RVB block at all, even though
    RVB was schema-offered the whole time (see `_relevant_modules`). Filling
    that gap here, once, after the model's own choices are finalized, means
    every generated preset gets a tasteful touch of it unless the request
    was explicitly dry. `_review_effect_sympathy` still applies its usual
    conservative Mix/Decay ceiling to whatever block lands here, exactly as
    it would to a model-chosen one.
    """
    if any(block["module"] == "RVB" for block in rig["signal_chain"]):
        return rig, ""
    if _wants_no_reverb(payload):
        return rig, ""
    reverb = _pick_reverb(payload, catalog)
    if reverb is None:
        return rig, ""
    blocks = deepcopy(rig["signal_chain"])
    blocks.append({
        "module": "RVB", "fxid": reverb["fxid"], "enabled": True,
        "purpose": "Default touch of reverb: not part of the AI plan, added because a real amp tone is rarely fully dry.",
        "parameters": {},
    })
    updated = validate_rig({"preset_name": rig["preset_name"], "summary": rig["summary"], "signal_chain": blocks}, catalog)
    if rig.get("validation_warning"):
        updated["validation_warning"] = rig["validation_warning"]
    note = f'{reverb["name"]} reverb added by default (not requested); kept subtle — adjust or remove it in the editor if you want a fully dry tone.'
    return updated, note


def _add_builtin_backbone(rig: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog) -> dict[str, Any]:
    """Supplement translated effects with a safe amp/cab when the model omitted them."""
    backbone = _builtin_fallback(payload, catalog)
    selected = {block["module"] for block in rig["signal_chain"]}
    blocks = [block for block in backbone["signal_chain"] if block["module"] not in selected] + rig["signal_chain"]
    completed = validate_rig({"preset_name": rig["preset_name"], "summary": rig["summary"], "signal_chain": blocks}, catalog)
    completed["validation_warning"] = (
        "Known effect roles were translated to GP-50 effects. The model omitted valid amp/cab IDs, so a safe built-in backbone was added."
    )
    return completed


def _relevant_modules(payload: dict[str, Any], catalog: GP50Catalog) -> list[str]:
    """Keep the model's catalogue short enough for reliable local inference.

    Matches free-text intent to modules using the same keyword lists
    (`GP50Catalog.keyword_modules`, sourced from the catalogue's own
    `effect_types`/`musical_profile` data) that `compact_for_prompt` sends
    the model, so the two cannot drift apart.
    """
    intent = payload.get("intent", {})
    effects = intent.get("effects", []) if isinstance(intent, dict) else []
    words = " ".join(
        " ".join(str(item.get(key, "")) for key in ("name", "purpose", "starting_point"))
        for item in effects if isinstance(item, dict)
    ).lower()
    modules: list[str] = []
    for module, keywords in catalog.keyword_modules().items():
        if any(keyword in words for keyword in keywords):
            modules.append(module)
    gain = str(intent.get("gain", "") if isinstance(intent, dict) else "").lower()
    if "high" in gain or any(term in words for term in ("high gain", "distortion", "fuzz", "heavy crunch")):
        modules.append("NR")
    # RVB is always offered, not gated on the interpreted intent naming it
    # explicitly: `make_search_plan`'s own instructions tell it to list only
    # effects that "meaningfully help" (deliberately conservative, to avoid
    # over-adding effects), so a request that doesn't literally say
    # "reverb"/"space"/etc. — most amp-based tone requests, even though a
    # touch of reverb is nearly always present in a real amp tone — would
    # otherwise never even offer a Reverb id in the schema's fxid enum,
    # making it schema-invalid for the model to add one no matter how
    # musically appropriate. Offering it, like AMP/CAB, doesn't force its
    # use — the model still decides whether/how much reverb actually fits.
    modules[0:0] = ["AMP", "CAB", "RVB"]
    seen: set[str] = set()
    return [m for m in modules if not (m in seen or seen.add(m))]


def _is_high_gain(payload: dict[str, Any], rig: dict[str, Any], catalog: GP50Catalog) -> bool:
    intent = payload.get("intent", {})
    text = " ".join([
        str(intent.get("gain", "") if isinstance(intent, dict) else ""),
        *(str(item) for item in (intent.get("character", []) if isinstance(intent, dict) else [])),
    ]).lower()
    if any(word in text for word in ("high", "heavy", "distortion", "fuzz", "saturated")):
        return True
    return any(catalog.get(block["fxid"])["type"] in {"Hi Gain", "Distortion", "Fuzz"} for block in rig["signal_chain"])


def _manage_gain(
    rig: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog,
    locked: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, Any]:
    """Apply conservative gain staging and add a real NR gate for high gain.

    `locked` names (module, parameter) pairs that `_apply_reference_settings`
    already set from an explicit sourced value for the exact device in use —
    those are left alone rather than pulled down to a generic safe ceiling,
    since a value confirmed to belong to this actual amp/pedal is more
    trustworthy than the conservative default this guard exists to enforce
    on the model's own unsourced guesses.
    """
    high_gain = _is_high_gain(payload, rig, catalog)
    blocks = deepcopy(rig["signal_chain"])
    amp = next((block for block in blocks if block["module"] == "AMP"), None)
    drive = next((block for block in blocks if block["module"] == "DST" and block["enabled"]), None)

    def set_safe(block: dict[str, Any] | None, key: str, value: float, maximum: float | None = None) -> None:
        if not block or (block["module"], key) in locked:
            return
        effect = catalog.get(block["fxid"])
        if key not in {param["name"] for param in effect["params"]}:
            return
        current = float(block["parameters"].get(key, value))
        block["parameters"][key] = min(current, maximum) if maximum is not None else current

    if high_gain:
        # Do not max both a pedal and an amp. Use the pedal primarily to shape
        # and tighten, while the amp provides the main saturation.
        set_safe(amp, "Gain", 55, 55 if drive else 65)
        set_safe(amp, "Gain 1", 50, 50 if drive else 60)
        set_safe(amp, "Gain 2", 50, 50 if drive else 60)
        set_safe(amp, "VOL", 50, 55)
        set_safe(drive, "Gain", 28, 35)
        set_safe(drive, "Fuzz", 32, 40)
        set_safe(drive, "VOL", 50, 55)

        gate = next((block for block in blocks if block["module"] == "NR"), None)
        if gate is None:
            effect = next((item for item in catalog.effects_for_modules(["NR"]).get("NR", []) if item["type"] == "Gate"), None)
            if effect:
                blocks.insert(0, {"module": "NR", "fxid": effect["fxid"], "enabled": True,
                                  "purpose": "Suppresses high-gain idle noise without choking sustained notes.",
                                  "parameters": {"THRE": 20}})
        else:
            set_safe(gate, "THRE", 20, 45)

    managed = validate_rig({"preset_name": rig["preset_name"], "summary": rig["summary"], "signal_chain": blocks}, catalog)
    if high_gain:
        managed["validation_warning"] = (
            "Gain managed: the amp supplies the main saturation, any drive is capped as a tightener, "
            "and a Gate is enabled at a conservative threshold (20). Adjust by ear for your pickups."
        )
    elif rig.get("validation_warning"):
        managed["validation_warning"] = rig["validation_warning"]
    return managed


def _review_effect_sympathy(
    rig: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog,
    locked: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, Any]:
    """Keep effects purposeful and give time effects restrained starting values.

    See `_manage_gain` for what `locked` means: a param an explicit sourced
    setting already set is reported as-is here, not pulled down to this
    function's generic conservative ceiling.
    """
    intent = payload.get("intent", {})
    declared_effects = intent.get("effects", []) if isinstance(intent, dict) else []
    # The model is already schema-constrained to the roles inferred from the
    # interpretation.  When an interpretation explicitly names effects, use
    # that as a second guard against an unrelated optional block.  Preserve
    # validated blocks for sparse/legacy requests: they may have been supplied
    # by an editor or a role-repair path rather than a complete AI intent.
    allowed = set(_relevant_modules(payload, catalog)) | {"AMP", "CAB"}
    blocks = [
        deepcopy(block) for block in rig["signal_chain"]
        if not declared_effects or block["module"] in allowed
    ]
    requested = " ".join(
        " ".join(str(effect.get(key, "")) for key in ("name", "purpose", "starting_point"))
        for effect in (intent.get("effects", []) if isinstance(intent, dict) else [])
        if isinstance(effect, dict)
    ).lower()
    notes: list[str] = []

    def conservative(block: dict[str, Any], names: tuple[str, ...], default: float, maximum: float) -> float | None:
        effect = catalog.get(block["fxid"])
        available = {param["name"] for param in effect["params"]}
        for name in names:
            if name in available:
                current = float(block["parameters"].get(name, default))
                value = current if (block["module"], name) in locked else min(current, maximum)
                block["parameters"][name] = value
                return value
        return None

    for block in blocks:
        module = block["module"]
        if module == "DLY":
            long = any(word in requested for word in ("long", "ambient", "wash", "dotted", "rhythmic"))
            mix = conservative(block, ("Mix",), 22, 30)
            feedback = conservative(block, ("F.Back", "Feedback"), 30 if long else 22, 45)
            conservative(block, ("Time",), 420 if long else 280, 650)
            notes.append(f"Delay retained for the request; mix {int(mix or 0)} and feedback {int(feedback or 0)} are kept below washout levels.")
        elif module == "RVB":
            spacious = any(word in requested for word in ("long", "large", "hall", "plate", "ambient", "space"))
            mix = conservative(block, ("Mix",), 20, 28)
            decay = conservative(block, ("Decay",), 42 if spacious else 30, 55)
            if not str(block.get("purpose", "")).startswith("Default touch of reverb"):
                notes.append(f"Reverb retained for the request; mix {int(mix or 0)} and decay {int(decay or 0)} preserve note definition.")
        elif module in {"MOD", "PRE", "DST", "EQ"}:
            notes.append(f"{block['effect_name']} retained because it matches a requested tonal role.")

    reviewed = validate_rig({"preset_name": rig["preset_name"], "summary": rig["summary"], "signal_chain": blocks}, catalog)
    if rig.get("validation_warning"):
        reviewed["validation_warning"] = rig["validation_warning"]
    reviewed["effect_review"] = notes or ["No optional effects were added beyond the amp/cab path."]
    return reviewed


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower()) if len(t) > 2}


def _apply_reference_settings(
    rig: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog
) -> tuple[dict[str, Any], list[str], frozenset[tuple[str, str]]]:
    """Apply an explicit, sourced numeric setting (e.g. "gain 6, bass 5, mid 6,
    treble 6.5" for a named amp, from web research) onto the matching GP-50
    parameter — deterministically, in code, rather than asking the model to
    eyeball a percentage inside the same generation that's also choosing
    effects and writing schema-constrained JSON. LLMs are well documented to
    be unreliable at exactly this kind of arithmetic; the model's own job
    (`tone_finder.make_search_plan`) is limited to the part that's genuinely
    semantic — reading "Manual 2.0" and knowing that's 20% of a 0-10 dial —
    and this function does the rescale onto the actual hardware range.

    A translated number is only trustworthy when the reported gear and the
    chosen GP-50 model actually share circuitry: a Fender tonestack scoops
    mids where a Marshall pushes them, so copying dial numbers across
    different amps is a documented way to get the wrong tone even though the
    numbers themselves are correct. A reference therefore only applies to a
    block whose catalogue name/origin names that same real device (matched by
    shared significant words — deliberately conservative, since a missed
    match just leaves the model's own musical choice in place, while a wrong
    match would silently mis-set a real parameter). Every other block is
    untouched.
    """
    intent = payload.get("intent", {})
    references = intent.get("reference_settings", []) if isinstance(intent, dict) else []
    if not isinstance(references, list) or not references:
        return rig, [], frozenset()
    blocks = deepcopy(rig["signal_chain"])
    notes: list[str] = []
    locked: set[tuple[str, str]] = set()
    for ref in references:
        if not isinstance(ref, dict):
            continue
        device = str(ref.get("device", "")).strip()
        device_tokens = _tokenize(device)
        controls = ref.get("controls", {})
        if not device_tokens or not isinstance(controls, dict):
            continue
        for block in blocks:
            effect = catalog.get(block.get("fxid"))
            if not effect or not (device_tokens & _tokenize(f"{effect.get('name', '')} {effect.get('origin', '')}")):
                continue
            applied = []
            for control_name, pct in controls.items():
                try:
                    pct = max(0.0, min(100.0, float(pct)))
                except (TypeError, ValueError):
                    continue
                param = find_parameter(effect, str(control_name))
                if param is None:
                    continue
                minimum, maximum = float(param.get("min", 0.0)), float(param.get("max", 100.0))
                try:
                    name, numeric = _coerce_parameter(effect, str(control_name), minimum + (pct / 100.0) * (maximum - minimum))
                except ValueError:
                    continue
                block["parameters"][name] = numeric
                applied.append(f"{name} {numeric:g}")
                # Downstream safety passes (_manage_gain's headroom cap,
                # _review_effect_sympathy's conservative delay/reverb
                # ceilings) must not pull a confirmed sourced value back down
                # to their generic default — that would silently discard the
                # one thing this function exists to preserve.
                locked.add((block["module"], name))
            if applied:
                notes.append(f'{effect["name"]}: applied sourced settings for "{device}" ({", ".join(applied)}).')
    if not notes:
        return rig, [], frozenset()
    reviewed = dict(rig)
    reviewed["signal_chain"] = blocks
    return reviewed, notes, frozenset(locked)


def _describe_signal_chain(signal_chain: list[dict[str, Any]], catalog: GP50Catalog) -> str:
    """Build a factual "Amp: X (Gain 55, Bass 50, ...) · Cab: Y · ..." recap
    straight from the final signal chain, in hardware order (MODULE_ORDER).

    The model writes its own `summary` before _manage_gain/_apply_reference_
    settings run, so its prose can describe numbers — "Gain cranked to 90" —
    that no longer match what's actually about to be downloaded once those
    safety/sourcing passes finish. This runs last and reports the real final
    values, so the summary a user reads is always accurate to the preset.
    """
    order = {module: i for i, module in enumerate(MODULE_ORDER)}
    parts = []
    for block in sorted(signal_chain, key=lambda b: order.get(b["module"], len(order))):
        if not block.get("enabled", True):
            continue
        name = block.get("effect_name") or block["module"]
        prefix = {"AMP": "Amp", "CAB": "Cab"}.get(block["module"], name)
        params = ", ".join(f"{k} {v:g}" for k, v in block.get("parameters", {}).items())
        label = f"{prefix}: {name}" if prefix != name else name
        parts.append(f"{label} ({params})" if params else label)
    return " · ".join(parts)


def _finalize_rig(rig: dict[str, Any], payload: dict[str, Any], catalog: GP50Catalog) -> dict[str, Any]:
    rig, reference_notes, locked = _apply_reference_settings(rig, payload, catalog)
    rig, reverb_note = _ensure_reverb(_manage_gain(rig, payload, catalog, locked), payload, catalog)
    finalized = _review_effect_sympathy(rig, payload, catalog, locked)
    if reference_notes:
        finalized["effect_review"] = reference_notes + list(finalized.get("effect_review", []))
    if reverb_note:
        finalized["effect_review"] = list(finalized.get("effect_review", [])) + [reverb_note]
    recap = _describe_signal_chain(finalized["signal_chain"], catalog)
    if recap:
        narrative = str(finalized.get("summary", "")).strip()
        finalized["summary"] = f"{recap}. {narrative}" if narrative else recap
    # The list itself (not just the text recap above) is what the UI renders
    # block-by-block and what "Advanced: edit preset data" shows — it was
    # left in whatever order the model happened to emit its JSON array (or
    # `_add_builtin_backbone` happened to concatenate blocks), which is
    # unrelated to the GP-50's actual NR->PRE->DST->AMP->CAB->EQ->MOD->DLY->
    # RVB signal path and could show, e.g., a PRE compressor listed after
    # the AMP. Sort by the same hardware order the recap text already uses,
    # so what a user reads always matches the real signal flow. This does
    # not affect the binary preset: create_preset writes each block to its
    # own fixed module_id byte offset regardless of list position.
    order = {module: i for i, module in enumerate(MODULE_ORDER)}
    finalized["signal_chain"] = sorted(finalized["signal_chain"], key=lambda b: order.get(b["module"], len(order)))
    return finalized


def _slot_sharing_guidance(catalog: GP50Catalog) -> str:
    """Spell out, from the catalogue's own module/type data (see
    `GP50Catalog.shared_effect_slots`), which modules bundle several
    unrelated effect types onto one physical slot — so the model is told
    explicitly, rather than left to infer it from the JSON grouping, that a
    request naming two effects landing in the same module needs a choice
    made between them, not both included. This text depends only on the
    catalogue (not the current request), so it's identical on every call —
    safe to prepend to the constant part of the prompt without hurting the
    KV-cache prefix match `build_rig` relies on (see its own comment on
    `gp50_catalogue` ordering).
    """
    # AMP/CAB also have multiple `type` values (Clean/Drive/Hi Gain, etc.),
    # but "pick exactly one amp model" is already obvious and separately
    # instructed just above — that's a single ordinary choice, not several
    # unrelated effect categories fighting for one slot. Limiting this note
    # to the genuinely surprising cases keeps the signal sharp instead of
    # diluting it (and keeps the prompt smaller — see CLAUDE.md on prompt size).
    shared = {module: types for module, types in catalog.shared_effect_slots().items() if module not in {"AMP", "CAB"}}
    if not shared:
        return ""
    parts = [f"{module} (one slot, choose from: {', '.join(types)})" for module, types in sorted(shared.items())]
    return (
        " The GP-50 has exactly one hardware slot per module. Some modules bundle several unrelated "
        "effect types onto that single shared slot rather than one type each: " + "; ".join(parts) + ". "
        "If the request calls for two effects that land in the same module (e.g. a compressor and a wah), "
        "the hardware genuinely cannot run both at once — choose whichever matters most for this tone and "
        "leave the other out, rather than trying to fit both in."
    )


def build_rig(payload: dict[str, Any], lm_json: Callable[..., Any], catalog: GP50Catalog | None = None) -> dict[str, Any]:
    catalog = catalog or default_catalog()
    modules = _relevant_modules(payload, catalog)
    guidance = (
        "Select one AMP and one CAB from the catalogue as the primary GP-50 amp/cab recommendation for this tone. "
        "Each catalogue effect's `profile` key looks up its functional description in `gp50_catalogue.legend` "
        "(shared across every effect of that type unless the key names a specific model); a param with no `unit` "
        "or `toggle` listed is a plain numeric control, not a switch. When "
        "`interpreted_tone_intent.reference_settings` names a real amp/pedal, prefer the catalogue model whose "
        "`origin`/`name` names that same device if it's musically appropriate — its sourced settings are applied "
        "automatically afterward, onto that specific block only; a different model is never given someone else's numbers."
        + _slot_sharing_guidance(catalog)
    )
    # AMP (32 models) and CAB (41) dwarf every other module and dominate
    # prompt size; narrow those to the highest-relevance-scored options
    # instead of sending the full roster on every request. Every other
    # module is already at or under compact_for_prompt's default cap and is
    # unaffected. gp50_catalogue is built once and both the prompt and the
    # fxid enum below are derived from it, so they can never drift apart —
    # an id the model wasn't shown a description for is never schema-valid.
    gp50_catalogue = catalog.compact_for_prompt(
        modules, terms=_fallback_terms(payload), target_tone=_target_tone_from_intent(payload),
    )
    # gp50_catalogue is the one part of this prompt that's ever identical
    # across calls (same narrowed modules => same JSON bytes); request/
    # research_notes/intent are unique to this call every time. mlx_lm.server
    # (and vLLM/llama.cpp) cache a request's KV state and reuse whatever
    # prefix of *tokens* matches a previous call, stopping at the first
    # difference — so the catalogue has to come first, or the tone-specific
    # text ahead of it invalidates the match before the expensive part is
    # ever reached. This ordering is what actually lets that cache help,
    # not just this call's retry (identical prompt + a short appended note)
    # but a later call that happens to narrow to the same catalogue.
    context = {
        "gp50_catalogue": gp50_catalogue,
        "request": payload.get("query", ""), "web_research_notes": payload.get("research_notes", ""),
        "interpreted_tone_intent": payload.get("intent", {}),
    }
    prompt = guidance + "\n\n" + json.dumps(context, ensure_ascii=False)
    last_error: RigValidationError | None = None
    # Constrain IDs at decoding time as well as validating after decoding. This
    # makes local models substantially less likely to manufacture an fxid.
    schema = deepcopy(RIG_SCHEMA)
    schema["properties"]["signal_chain"]["items"]["properties"]["fxid"]["enum"] = sorted(
        effect["id"] for effects in gp50_catalogue["modules"].values() for effect in effects
    )
    schema["properties"]["signal_chain"]["items"]["properties"]["module"]["enum"] = modules
    last_result: dict[str, Any] = {}
    for attempt in range(2):
        retry = "" if attempt == 0 else "\n\nYour last plan was rejected. Correct every listed error and return a complete replacement JSON:\n" + "\n".join(last_error.errors)
        result = lm_json(SYSTEM, prompt + retry, schema, temperature=0.15 if attempt == 0 else 0)
        last_result = result
        result["preset_name"] = _safe_preset_name(result.get("preset_name"))
        # The schema only constrains *which* fxids are valid, not that two of
        # them don't want the same physical module — a request that
        # legitimately calls for several PRE-type roles at once (wah, octave,
        # boost) can make the model return more than one. Resolve that here,
        # by relevance to this request, instead of letting validate_rig's
        # hard "GP-50 has only one X block" error burn a retry (or fall all
        # the way to salvage) over something with a principled answer.
        if isinstance(result.get("signal_chain"), list):
            result["signal_chain"] = _keep_best_per_module(result["signal_chain"], payload, catalog)
        try:
            rig = validate_rig(result, catalog)
            selected = {block["module"] for block in rig["signal_chain"]}
            missing = [module for module in ("AMP", "CAB") if module not in selected]
            if missing:
                raise RigValidationError(["Built-in amp/cab mode requires " + " and ".join(missing)])
            return _finalize_rig(rig, payload, catalog)
        except RigValidationError as exc:
            last_error = exc
    salvaged = _salvage_valid_blocks(last_result, catalog, payload)
    if salvaged:
        selected = {block["module"] for block in salvaged["signal_chain"]}
        missing = [module for module in ("AMP", "CAB") if module not in selected]
        if missing:
            return _finalize_rig(_add_builtin_backbone(salvaged, payload, catalog), payload, catalog)
        return _finalize_rig(salvaged, payload, catalog)
    return _finalize_rig(_builtin_fallback(payload, catalog), payload, catalog)
