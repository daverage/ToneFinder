# GP-50 data

`gp50_catalog.json` and `gp50_catalog_llm.json` are supplied at the project
root by the catalogue helper package. For a distributable build, generate or
copy them into this directory with:

```sh
python gp50_catalog_builder/build_gp50_catalog.py \
  --output data/gp50_catalog.json \
  --llm-output data/gp50_catalog_llm.json
```

Place a known-good, empty 552-byte GP-50 export at `data/blank_gp50.prst`.
The application will not manufacture one: preserving undocumented bytes from a
real Valeton Suite export is intentional.
