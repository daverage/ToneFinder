# GP-50 catalogue builder

This is intended to be dropped into the new AI Tone Finder project. It is
independent of NamToClo.

## What it does

`build_gp50_catalog.py` obtains the reverse-engineered GP-50 effect catalogue
from:

- repository: `drewmerc302/valeton-gp50`
- source file: `patch/fxid_ring.json`

It writes:

- `gp50_catalog.json` — full normalized catalogue for deterministic validation
- `gp50_catalog_llm.json` — smaller representation suitable for supplying to LM Studio

Each model retains:

- exact FX ID
- module and module ID
- GP-50 name/title
- effect type
- real-world origin where known
- parameter names
- parameter slot (`alg_id`)
- min/max/step/default
- units
- toggle status

## Setup

No third-party Python packages are required.

```bash
python build_gp50_catalog.py
```

If you have already downloaded the source catalogue:

```bash
python build_gp50_catalog.py --source fxid_ring.json
```

## Use from the Tone Finder

```python
from gp50_catalog import GP50Catalog, compact_catalog_for_prompt

catalog = GP50Catalog()

# Deterministic lookup
green_od = catalog.find("DST", "Green OD")

# Give the LLM only the effect families that are useful for this request.
available = compact_catalog_for_prompt(
    modules=["PRE", "DST", "MOD", "DLY", "RVB"]
)
```

Then include `available` in the LM Studio prompt and explicitly instruct it:

> Select only GP-50 effects and parameter names present in the supplied
> catalogue. Never invent an effect, model ID, block or parameter.

Do not trust the LLM output directly. Validate the selected `fxid`, parameter
names and values with `GP50Catalog` before generating a `.prst`.

## Recommended AI output

The next stage should use structured JSON resembling:

```json
{
  "preset_name": "Comfortably Numb",
  "signal_chain": [
    {
      "module": "DST",
      "fxid": 50331648,
      "enabled": true,
      "parameters": {
        "Gain": 12,
        "Tone": 55,
        "VOL": 75
      }
    }
  ]
}
```

The AI chooses the rig. Python remains responsible for validating it and,
later, translating it into the binary `.prst` format.

## Why there are two catalogues

The full file is intended to be the source of truth for the application.

The LLM version omits binary/validation-specific metadata that the model does
not need. In the actual application, prefer sending only the relevant modules
for a request rather than all available models every time.
