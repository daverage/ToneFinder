"""Strict validation of LLM-created GP-50 rig plans."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .catalog import GP50Catalog, canonical_module, default_catalog

MODULE_ORDER = ["NR", "PRE", "DST", "N->S", "AMP", "CAB", "EQ", "MOD", "DLY", "RVB"]


class RigValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# Guitar-gear vocabulary is standardized enough (every amp/pedal manual and
# tone-recipe site uses these same short forms) that treating them as
# genuinely the same control is safe — unlike the general punctuation/casing
# tolerance below, this is a fixed, closed list of real synonyms, not a fuzzy
# guess. Every group's own canonical members are included so lookup doesn't
# need to know which side (query or catalogue) used the long form.
_PARAM_ALIAS_GROUPS = [
    {"mid", "middle", "mids"}, {"pres", "presence"}, {"vol", "volume"},
    {"treble", "trebles", "hi", "high"}, {"bass", "low", "lows"},
    {"fback", "feedback", "fb"}, {"rtn", "return"}, {"snd", "send"},
]
_PARAM_ALIASES = {member: group for group in _PARAM_ALIAS_GROUPS for member in group}


def find_parameter(effect: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Match a parameter name against an effect's params, tolerating a
    snake_case/punctuation variant of the display label (e.g. "Tone Cut" ->
    "tone_cut") and the standard short/long forms of common guitar controls
    (e.g. "Presence" -> "PRES", "Mid" -> "Middle"). Never maps an invented
    control to a different real parameter: an ambiguous or unrecognized name
    returns None rather than guessing. Shared by `_coerce_parameter` and by
    the reference-settings matcher in `gp50.rig_builder`, so "what counts as
    this control" has one definition.
    """
    param = next((p for p in effect["params"] if p["name"].lower() == name.lower()), None)
    if param is not None:
        return param
    canonical = re.sub(r"[^a-z0-9]+", "", name.lower())
    candidates = _PARAM_ALIASES.get(canonical, {canonical})
    matches = [
        p for p in effect["params"]
        if re.sub(r"[^a-z0-9]+", "", p["name"].lower()) in candidates
    ]
    return matches[0] if len(matches) == 1 else None


def _coerce_parameter(effect: dict[str, Any], name: str, value: Any) -> tuple[str, float]:
    param = find_parameter(effect, name)
    if param is None:
        raise ValueError(f"{effect['name']} has no parameter named {name!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{effect['name']} {param['name']} must be numeric") from exc
    minimum, maximum = param.get("min"), param.get("max")
    if minimum is not None and numeric < float(minimum):
        numeric = float(minimum)
    if maximum is not None and numeric > float(maximum):
        numeric = float(maximum)
    step = param.get("step")
    if step:
        base = float(minimum or 0)
        numeric = base + round((numeric - base) / float(step)) * float(step)
        if minimum is not None: numeric = max(float(minimum), numeric)
        if maximum is not None: numeric = min(float(maximum), numeric)
    if param.get("toggle"):
        numeric = 1.0 if numeric >= 0.5 else 0.0
    return param["name"], numeric


def validate_rig(plan: dict[str, Any], catalog: GP50Catalog | None = None) -> dict[str, Any]:
    """Return a normalized safe plan, or enumerate every invalid choice."""
    catalog = catalog or default_catalog()
    plan = deepcopy(plan)
    errors: list[str] = []
    name = str(plan.get("preset_name", "")).strip()
    if not name:
        errors.append("preset_name is required")
    elif len(name.encode("latin-1", errors="replace")) > 15:
        errors.append("preset_name exceeds the GP-50 15-character display limit")
    chain = plan.get("signal_chain")
    if not isinstance(chain, list) or len(chain) > 10:
        errors.append("signal_chain must contain at most 10 blocks")
        chain = []
    output, used_modules = [], set()
    for index, block in enumerate(chain):
        if not isinstance(block, dict):
            errors.append(f"signal_chain[{index}] must be an object")
            continue
        effect = catalog.get(block.get("fxid"))
        if effect is None:
            errors.append(f"signal_chain[{index}] has unknown fxid {block.get('fxid')!r}")
            continue
        module = canonical_module(block.get("module"))
        if module != canonical_module(effect["module"]):
            errors.append(f"signal_chain[{index}] module {module!r} does not match fxid {effect['fxid']} ({effect['module']})")
            continue
        if module in used_modules:
            errors.append(f"GP-50 has only one {module} block")
            continue
        used_modules.add(module)
        if not isinstance(block.get("enabled"), bool):
            errors.append(f"signal_chain[{index}] enabled must be boolean")
            continue
        values = block.get("parameters", {})
        if not isinstance(values, dict):
            errors.append(f"signal_chain[{index}] parameters must be an object")
            continue
        normalized = {}
        for param_name, value in values.items():
            try:
                key, numeric = _coerce_parameter(effect, str(param_name), value)
                normalized[key] = numeric
            except ValueError as exc:
                errors.append(f"signal_chain[{index}]: {exc}")
        # Any parameter the plan didn't set gets the catalogue's own
        # documented default rather than being left out of `parameters`: the
        # GP-50 binary preset only writes bytes for params present in this
        # dict (see gp50.preset.create_preset), so an omitted parameter
        # doesn't mean "use the hardware default" — it silently keeps
        # whatever value the *blank template* happens to have at that byte
        # offset, which is unrelated to the effect actually selected. That
        # bit users and a salvaged/partial AI plan (missing or emptied
        # `parameters`) most often hit.
        for parameter in effect["params"]:
            normalized.setdefault(parameter["name"], float(parameter["default"]))
        output.append({"module": module, "fxid": int(effect["fxid"]), "enabled": block["enabled"],
                       "purpose": str(block.get("purpose", "")).strip(), "parameters": normalized,
                       "effect_name": effect["name"], "origin": effect.get("origin", "")})
    if errors:
        raise RigValidationError(errors)
    return {"preset_name": name, "summary": str(plan.get("summary") or "").strip(), "signal_chain": output}
