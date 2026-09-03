#!/usr/bin/env python3
"""
Build an AI-friendly GP-50 effects catalogue from the reverse-engineered
drewmerc302/valeton-gp50 fxid_ring.json.

Usage:
    python build_gp50_catalog.py

Optional:
    python build_gp50_catalog.py --source ./fxid_ring.json
    python build_gp50_catalog.py --output ./gp50_catalog.json

By default the script downloads:
https://raw.githubusercontent.com/drewmerc302/valeton-gp50/master/patch/fxid_ring.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "drewmerc302/valeton-gp50/master/patch/fxid_ring.json"
)

KNOWN_MODULE_ORDER = [
    "NR", "PRE", "DST", "N>S", "AMP", "CAB", "EQ", "MOD", "DLY", "RVB"
]


def load_source(source: str | None) -> dict[str, Any]:
    if source:
        p = Path(source)
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    req = Request(
        SOURCE_URL,
        headers={"User-Agent": "gp50-catalog-builder/1.0"},
    )
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def norm_param(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(p.get("name", "")).strip(),
        "alg_id": int(p.get("algId", 0)),
        "toggle": bool(p.get("toggle", False)),
        "unit": str(p.get("unit", "") or ""),
        "min": p.get("min"),
        "max": p.get("max"),
        "step": p.get("step"),
        "default": p.get("default"),
    }


def norm_effect(raw_id: str, e: dict[str, Any]) -> dict[str, Any]:
    fxid = int(raw_id)

    return {
        "fxid": fxid,
        "fxid_hex": f"0x{fxid:08X}",
        "module": str(e.get("module", "")).strip(),
        "module_id": int(e.get("moduleId", -1)),
        "name": str(e.get("name", "")).strip(),
        "title": str(e.get("fxtitle", "")).strip(),
        "type": str(e.get("type", "")).strip(),
        "origin": str(e.get("origin", "")).strip(),
        "params": sorted(
            [norm_param(p) for p in (e.get("params") or [])],
            key=lambda p: p["alg_id"],
        ),
    }


def validate_effect(e: dict[str, Any]) -> list[str]:
    problems = []

    if not e["module"]:
        problems.append("missing module")
    if not e["name"]:
        problems.append("missing name")
    if e["module_id"] < 0:
        problems.append("invalid module_id")

    seen = set()
    for p in e["params"]:
        aid = p["alg_id"]
        if aid in seen:
            problems.append(f"duplicate alg_id {aid}")
        seen.add(aid)

        if aid < 0 or aid > 7:
            problems.append(f"alg_id {aid} outside GP-50 0..7 parameter slots")

        mn, mx, default = p["min"], p["max"], p["default"]
        if mn is not None and mx is not None and mn > mx:
            problems.append(f"{p['name']}: min > max")

        if (
            default is not None
            and mn is not None
            and mx is not None
            and not (mn <= default <= mx)
        ):
            problems.append(
                f"{p['name']}: default {default} outside {mn}..{mx}"
            )

    return problems


def ai_summary(effect: dict[str, Any]) -> dict[str, Any]:
    """
    Compact record suitable for sending to an LLM.
    Keeps only fields the model needs when choosing a rig.
    """
    return {
        "id": effect["fxid"],
        "module": effect["module"],
        "name": effect["name"],
        "type": effect["type"],
        "origin": effect["origin"],
        "params": [
            {
                "name": p["name"],
                "min": p["min"],
                "max": p["max"],
                "default": p["default"],
                "unit": p["unit"],
                "toggle": p["toggle"],
            }
            for p in effect["params"]
        ],
    }


def build_catalog(src: dict[str, Any]) -> dict[str, Any]:
    effects = [norm_effect(raw_id, e) for raw_id, e in src.items()]
    effects.sort(
        key=lambda e: (
            KNOWN_MODULE_ORDER.index(e["module"])
            if e["module"] in KNOWN_MODULE_ORDER
            else 999,
            e["type"].lower(),
            e["name"].lower(),
            e["fxid"],
        )
    )

    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_type: dict[str, list[int]] = defaultdict(list)
    validation: dict[str, list[str]] = {}

    for e in effects:
        by_module[e["module"]].append(e)
        if e["type"]:
            by_type[e["type"]].append(e["fxid"])

        problems = validate_effect(e)
        if problems:
            validation[str(e["fxid"])] = problems

    module_counts = {
        module: len(by_module[module])
        for module in sorted(
            by_module,
            key=lambda m: (
                KNOWN_MODULE_ORDER.index(m)
                if m in KNOWN_MODULE_ORDER
                else 999,
                m,
            ),
        )
    }

    return {
        "schema_version": 1,
        "device": "Valeton GP-50",
        "source": {
            "project": "drewmerc302/valeton-gp50",
            "file": "patch/fxid_ring.json",
            "url": SOURCE_URL,
            "note": (
                "Reverse-engineered catalogue. Keep the original project attribution "
                "and verify unusual parameter mappings on hardware where needed."
            ),
        },
        "stats": {
            "effect_count": len(effects),
            "module_counts": module_counts,
            "validation_warning_count": len(validation),
        },
        "module_order": KNOWN_MODULE_ORDER,
        "modules": dict(by_module),
        "types": dict(sorted(by_type.items())),
        "validation_warnings": validation,
    }


def make_llm_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "device": catalog["device"],
        "module_order": catalog["module_order"],
        "modules": {
            module: [ai_summary(e) for e in effects]
            for module, effects in catalog["modules"].items()
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--source",
        help="Local fxid_ring.json. If omitted, downloads from GitHub.",
    )
    ap.add_argument(
        "--output",
        default="gp50_catalog.json",
        help="Full normalized output JSON.",
    )
    ap.add_argument(
        "--llm-output",
        default="gp50_catalog_llm.json",
        help="Compact catalogue intended for LLM prompting.",
    )
    args = ap.parse_args()

    try:
        src = load_source(args.source)
        catalog = build_catalog(src)
        llm_catalog = make_llm_catalog(catalog)

        Path(args.output).write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        Path(args.llm_output).write_text(
            json.dumps(llm_catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(f"Wrote {args.output}")
        print(f"Wrote {args.llm_output}")
        print(f"Effects: {catalog['stats']['effect_count']}")
        print("Modules:")
        for k, v in catalog["stats"]["module_counts"].items():
            print(f"  {k:4s} {v}")
        if catalog["stats"]["validation_warning_count"]:
            print(
                "Validation warnings:",
                catalog["stats"]["validation_warning_count"],
                file=sys.stderr,
            )
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
