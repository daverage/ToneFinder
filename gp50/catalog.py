"""Access the normalized GP-50 catalogue; it is the sole hardware AND musical
knowledge authority. `gp50_catalog.json` carries both, so this module
interprets the catalogue rather than hardcoding musical knowledge itself."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Fallback text used only if a catalogue somehow lacks "effect_types" for a
# type this code encounters (e.g. an older catalogue file during migration).
_FALLBACK_FUNCTION = "Use according to the documented controls."

_STOPWORDS = {"the", "and", "for", "with", "that", "this", "into", "from", "your", "you", "are"}


def tokenize(text: str) -> set[str]:
    """Lowercase word set for matching free-text intent against catalogue
    metadata. Shared by scoring and by callers that build `terms` so
    tokenization can't drift between them."""
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) > 2 and w not in _STOPWORDS}


def _phrase_words(phrase: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(phrase or "").lower())


def _phrase_hits(phrases: list[str], terms: set[str]) -> tuple[int, int]:
    """Count (full_phrase_matches, single_word_matches) of `phrases` against
    `terms`. A multi-word phrase counts as a full match only when every one
    of its words is present in `terms` — the deterministic stand-in for
    matching a phrase like "edge of breakup" or "dotted eighth" without an
    embedding model."""
    full = 0
    partial = 0
    for phrase in phrases:
        words = _phrase_words(phrase)
        if not words:
            continue
        hits = sum(1 for w in words if w in terms)
        if hits == len(words):
            full += 1
        elif hits:
            partial += 1
    return full, partial


def score_effect_relevance(
    effect: dict[str, Any], terms: set[str], type_profile: dict[str, Any] | None = None,
    target_tone: dict[str, float] | None = None,
) -> float:
    """How relevant this catalogue entry is to a user's free-text intent.

    `terms` is a lowercased word set (see `tokenize`). Weighted so an exact
    origin/model/name match dominates, roles/keywords/character/best_for
    contribute in decreasing order, and generic descriptive words contribute
    only a little — deliberately not "concatenate everything and count
    substrings equally". Deterministic and dependency-free: no embeddings.
    """
    if not terms and not target_tone:
        return 0.0
    score = 0.0
    name_words = tokenize(effect.get("name", ""))
    type_words = tokenize(effect.get("type", ""))
    origin_words = tokenize(effect.get("origin", ""))

    # Exact origin/model or name match: very high.
    score += 6 * len(name_words & terms)
    score += 5 * len(origin_words & terms)
    # Type match: high.
    score += 4 * len(type_words & terms)

    profile = dict(type_profile or {})
    for key, value in (effect.get("musical_profile") or {}).items():
        if key in ("keywords", "character", "best_for", "watch_out") and isinstance(value, list) and isinstance(profile.get(key), list):
            profile[key] = [*profile[key], *value]
        else:
            profile[key] = value

    roles = profile.get("roles") or []
    if roles:
        role_words = {w for role in roles for w in tokenize(str(role).replace("_", " "))}
        score += 4 * len(role_words & terms)

    keywords = profile.get("keywords") or []
    full, partial = _phrase_hits([str(k) for k in keywords], terms)
    score += 4 * full + 2 * partial

    character = profile.get("character") or []
    full, partial = _phrase_hits([str(c) for c in character], terms)
    score += 3 * full + 1.5 * partial

    best_for = profile.get("best_for") or []
    full, partial = _phrase_hits([str(b) for b in best_for], terms)
    score += 2 * full + 1 * partial

    description_words = tokenize(profile.get("what_it_does", ""))
    score += 0.5 * len(description_words & terms)

    if target_tone:
        tone = effect.get("tone") or {}
        shared = [k for k in target_tone if k in tone]
        if shared:
            distance = sum(abs(float(tone[k]) - float(target_tone[k])) for k in shared) / len(shared)
            score += 3 * (1 - distance)

    return score


def canonical_module(module: str) -> str:
    """Accept the human-friendly SnapTone spelling without changing source data."""
    value = str(module or "").strip().upper()
    return "N->S" if value in {"N>S", "N->S", "N–>S"} else value


_default_catalog_cache: dict[str, "GP50Catalog"] = {}


def default_catalog() -> "GP50Catalog":
    """Process-wide cached catalogue for callers that don't need a specific
    file (every internal `catalog or GP50Catalog()` default used to re-read
    and re-parse the ~400KB gp50_catalog.json and rebuild its fxid index on
    every single request — build_rig, validate_rig, create_preset each did
    this independently. The catalogue is immutable read-only data for the
    life of the process, so that was pure per-request allocation churn (and,
    on CPython, growth that the allocator rarely hands back to the OS) with
    no benefit. Call `GP50Catalog(path)` directly when a specific file is
    actually needed (tests pin an explicit path for isolation)."""
    cached = _default_catalog_cache.get("default")
    if cached is None:
        cached = _default_catalog_cache["default"] = GP50Catalog()
    return cached


class GP50Catalog:
    def __init__(self, filename: str | Path | None = None):
        preferred = Path(__file__).parents[1] / "data" / "gp50_catalog.json"
        # The supplied helper archive stores its generated catalogue at project root.
        # Prefer data/ for packaged releases, while remaining compatible with it.
        self.path = Path(filename) if filename else (preferred if preferred.is_file() else Path(__file__).parents[1] / "gp50_catalog.json")
        if not self.path.is_file():
            raise FileNotFoundError(f"GP-50 catalogue is missing: {self.path}")
        self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        self.effect_types: dict[str, Any] = self.data.get("effect_types", {})
        self.by_id: dict[int, dict[str, Any]] = {}
        for effects in self.data.get("modules", {}).values():
            for effect in effects:
                self.by_id[int(effect["fxid"])] = effect

    def get(self, fxid: int) -> dict[str, Any] | None:
        try:
            return self.by_id.get(int(fxid))
        except (TypeError, ValueError):
            return None

    def effects_for_modules(self, modules: list[str]) -> dict[str, list[dict[str, Any]]]:
        wanted = {canonical_module(m) for m in modules}
        return {m: effects for m, effects in self.data["modules"].items() if canonical_module(m) in wanted}

    def module_id(self, module: str) -> int:
        """The GP-50's fixed binary slot index for a module (every effect in a
        module shares one). Single source of truth for `gp50.preset`'s
        models/bypass/order records instead of re-deriving or hardcoding it.
        """
        wanted = canonical_module(module)
        for candidate, effects in self.data["modules"].items():
            if canonical_module(candidate) == wanted and effects:
                return int(effects[0]["module_id"])
        raise KeyError(f"Unknown GP-50 module: {module!r}")

    def type_profile(self, effect_type: str) -> dict[str, Any]:
        """Generic musical description shared by every model of this type."""
        return dict(self.effect_types.get(effect_type, {}))

    _MERGE_LIST_FIELDS = ("keywords", "character", "best_for", "watch_out")

    def musical_profile(self, effect: dict[str, Any]) -> dict[str, Any]:
        """Musician-facing matching metadata for a catalogue item: the
        generic `effect_types[type]` description, supplemented by the
        entry's own `musical_profile` (a well-identified model's specific
        character). List fields (keywords/character/best_for/watch_out) are
        merged rather than replaced, so a specific model still matches the
        type's generic search terms (e.g. every Delay entry stays matchable
        on "delay") in addition to its own; scalar fields like
        `what_it_does` are overridden by the more specific text. Falls back
        to a bare-bones profile if the catalogue has neither."""
        profile = self.type_profile(effect.get("type"))
        specific = effect.get("musical_profile") or {}
        for key, value in specific.items():
            if key in self._MERGE_LIST_FIELDS and isinstance(value, list) and isinstance(profile.get(key), list):
                profile[key] = list(dict.fromkeys([*profile[key], *value]))
            else:
                profile[key] = value
        if not profile:
            profile = {"what_it_does": _FALLBACK_FUNCTION, "keywords": []}
        return profile

    def keyword_modules(self) -> dict[str, set[str]]:
        """Aggregate each module's musical-profile keywords, from the same source
        `compact_for_prompt` sends the model, so free-text intent matching (see
        `gp50.rig_builder._relevant_modules`) cannot drift out of sync with it."""
        modules: dict[str, set[str]] = {}
        for effects in self.data.get("modules", {}).values():
            for effect in effects:
                keywords = self.musical_profile(effect).get("keywords") or []
                if keywords:
                    modules.setdefault(effect["module"], set()).update(k.lower() for k in keywords)
        return modules

    def _profile_label(self, effect: dict[str, Any]) -> str:
        """Key identifying this effect's functional description in the prompt
        legend: most effects share one entry per `type`, but models with
        their own catalogue `musical_profile` (a well-identified real-world
        device) get their own more precise entry, so those must not collapse
        into their type's generic text."""
        name = effect.get("name")
        return f"{effect.get('type')}:{name}" if effect.get("musical_profile") else str(effect.get("type"))

    _PROMPT_LIST_CAP = 4

    @staticmethod
    def _prompt_profile(effect: dict[str, Any]) -> dict[str, Any]:
        """Trimmed, entry-specific-only view of `musical_profile` for the LLM
        prompt: the generic type description already covers the shared
        ground (see `function` in the legend), so this must not repeat it.
        Drops `keywords` (module-routing/scoring metadata the LLM has no use
        for) and `watch_out` (a nice-to-have, not essential to a first-pass
        choice) and caps list fields, instead of forwarding the full merged
        profile `musical_profile()`/`score_effect_relevance` use internally —
        that full profile is what made every entry's prompt legend text
        balloon to ~1KB once most of the catalogue got its own specific
        profile, which is exactly the "repeated generic metadata" bloat this
        method exists to avoid."""
        specific = effect.get("musical_profile") or {}
        profile: dict[str, Any] = {}
        if specific.get("what_it_does"):
            profile["what_it_does"] = specific["what_it_does"]
        for key in ("character", "best_for"):
            values = specific.get(key)
            if values:
                profile[key] = list(values)[: GP50Catalog._PROMPT_LIST_CAP]
        if specific.get("roles"):
            profile["roles"] = list(specific["roles"])
        return profile

    @staticmethod
    def _compact_params(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop per-param keys at their near-universal default (`toggle`:
        false, `unit`: "") instead of repeating them on every one of a
        catalogue's ~800 params — the model only needs to be told about the
        rare exceptions. `semantic` metadata is kept whenever present since
        it's exactly the non-obvious behaviour the model needs flagged."""
        compact = []
        for p in params:
            entry = {k: p.get(k) for k in ("name", "min", "max", "step", "default")}
            if p.get("unit"):
                entry["unit"] = p["unit"]
            if p.get("toggle"):
                entry["toggle"] = True
            if p.get("semantic"):
                entry["semantic"] = p["semantic"]
            compact.append(entry)
        return compact

    def compact_for_prompt(
        self, modules: list[str], terms: set[str] | None = None, limit_per_module: int = 12,
        target_tone: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Only musical selection data is sent to the model, never binary metadata.

        AMP (32 models) and CAB (41) dwarf every other module and dominate
        prompt size; the rest are already at or under limit_per_module and are
        unaffected. When `terms` and/or `target_tone` are given, a module
        with more than limit_per_module entries is narrowed to its
        highest-scoring ones (score_effect_relevance, catalogue order breaks
        ties) rather than sending its full roster on every request — real
        musical choice among the most relevant options, not the entire
        catalogue every time. `target_tone` is a normalized tone vector (see
        each entry's own `tone` field) used to rank by sonic similarity even
        when the request's words don't literally appear in the catalogue.

        Every effect's functional description (`function` + `musical_profile`)
        is duplicated across every model of the same type — e.g. all ~10 OD
        pedals repeat the same ~250-character profile verbatim. Sending that
        text once per distinct label in a top-level `legend`, referenced from
        each effect by a short `profile` key, cuts that duplication out
        entirely instead of paying for it on every request. Models with their
        own catalogue-level `musical_profile` (a well-identified device) get
        their own legend entry instead of collapsing into the generic type
        text, so that specific knowledge is never lost.
        """
        effects_by_module = self.effects_for_modules(modules)
        if terms or target_tone:
            effects_by_module = {
                module: (
                    sorted(
                        effects,
                        key=lambda e: score_effect_relevance(
                            e, terms or set(), self.type_profile(e.get("type")), target_tone=target_tone,
                        ),
                        reverse=True,
                    )[:limit_per_module]
                    if len(effects) > limit_per_module else effects
                )
                for module, effects in effects_by_module.items()
            }
        legend: dict[str, Any] = {}
        modules_out: dict[str, Any] = {}
        for module, effects in effects_by_module.items():
            entries = []
            for e in effects:
                label = self._profile_label(e)
                if label not in legend:
                    type_profile = self.type_profile(e["type"])
                    entry_legend = {"function": type_profile.get("what_it_does", _FALLBACK_FUNCTION)}
                    prompt_profile = self._prompt_profile(e)
                    if prompt_profile:
                        entry_legend["musical_profile"] = prompt_profile
                    legend[label] = entry_legend
                out = {
                    "id": e["fxid"], "module": e["module"], "name": e["name"],
                    "type": e["type"], "origin": e["origin"], "profile": label,
                    "params": self._compact_params(e["params"]),
                }
                if e.get("tone"):
                    out["tone"] = e["tone"]
                entries.append(out)
            modules_out[module] = entries
        return {"device": "Valeton GP-50", "legend": legend, "modules": modules_out}
