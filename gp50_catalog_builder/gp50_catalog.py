"""
Helpers for using gp50_catalog.json / gp50_catalog_llm.json
inside the AI Tone Finder project.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class GP50Catalog:
    def __init__(self, filename: str = "gp50_catalog.json"):
        self.path = Path(filename)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

        self.by_id: dict[int, dict[str, Any]] = {}
        self.by_module_name: dict[tuple[str, str], dict[str, Any]] = {}

        for module, effects in self.data["modules"].items():
            for effect in effects:
                self.by_id[int(effect["fxid"])] = effect
                self.by_module_name[(module.upper(), effect["name"].lower())] = effect

    def get(self, fxid: int) -> dict[str, Any] | None:
        return self.by_id.get(int(fxid))

    def find(self, module: str, name: str) -> dict[str, Any] | None:
        return self.by_module_name.get((module.upper(), name.lower()))

    def effects_for_module(self, module: str) -> list[dict[str, Any]]:
        return list(self.data["modules"].get(module.upper(), []))

    def validate_setting(
        self,
        fxid: int,
        param_name: str,
        value: float,
    ) -> tuple[bool, str]:
        effect = self.get(fxid)
        if not effect:
            return False, f"Unknown GP-50 fxid {fxid}"

        for p in effect["params"]:
            if p["name"].lower() == param_name.lower():
                mn, mx = p["min"], p["max"]
                if mn is not None and value < mn:
                    return False, f"{param_name} must be >= {mn}"
                if mx is not None and value > mx:
                    return False, f"{param_name} must be <= {mx}"
                return True, ""

        return False, f"{effect['name']} has no parameter named {param_name!r}"

    def clamp_setting(
        self,
        fxid: int,
        param_name: str,
        value: float,
    ) -> float:
        effect = self.get(fxid)
        if not effect:
            raise KeyError(f"Unknown GP-50 fxid {fxid}")

        for p in effect["params"]:
            if p["name"].lower() == param_name.lower():
                x = float(value)
                if p["min"] is not None:
                    x = max(float(p["min"]), x)
                if p["max"] is not None:
                    x = min(float(p["max"]), x)
                step = p.get("step")
                if step:
                    base = float(p["min"] or 0)
                    x = base + round((x - base) / float(step)) * float(step)
                return x

        raise KeyError(f"{effect['name']} has no parameter {param_name!r}")


def compact_catalog_for_prompt(
    filename: str = "gp50_catalog_llm.json",
    modules: list[str] | None = None,
) -> str:
    """
    Return compact JSON to inject into an LM Studio prompt.

    Important: normally pass only relevant modules, e.g.
        ["PRE", "DST", "MOD", "DLY", "RVB"]
    rather than the entire catalogue.
    """
    data = json.loads(Path(filename).read_text(encoding="utf-8"))

    if modules:
        wanted = {m.upper() for m in modules}
        data["modules"] = {
            k: v for k, v in data["modules"].items() if k.upper() in wanted
        }

    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
