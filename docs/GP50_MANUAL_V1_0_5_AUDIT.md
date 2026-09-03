# GP-50 manual audit - firmware 1.0.5

Source: `GP-50_Online Manual_EN_Firmware V1.0.5_251128.pdf`, Effect List,
manual pages 31-48. The manual is the authority for names, effect descriptions,
and UI-visible controls. The existing `gp50_catalog.json` remains the authority
for binary FX IDs, parameter slots (`alg_id`), numeric ranges, and defaults until
those values are verified from a GP-50 export or readback.

## Model inventory

The documented model counts match the current catalogue exactly:

| Module | Manual / catalogue models |
| --- | ---: |
| NR | 1 |
| PRE | 12 |
| DST | 10 |
| N->S | 1 documented control layout (80 capture slots in the catalogue) |
| AMP | 32 |
| CAB | 41 (20 User IR slots included) |
| EQ | 5 |
| MOD | 11 |
| DLY | 10 |
| RVB | 10 |

## Confirmed catalogue issues

The following discrepancies were found by comparing the Effect List to the
catalogue. Do not infer a binary parameter-slot remap from the manual: it does
not document `alg_id` values.

| Model | Manual controls | Current catalogue controls | Severity |
| --- | --- | --- | --- |
| B-Boost (PRE) | Gain, Tone, VOL | Gain, VOL, Bass, Treble | High: differing control count and names |
| Yellow OD (DST) | Gain, Tone, VOL | Corrected from an exported native preset: slot 0 Gain, slot 1 Tone, slot 2 VOL | Resolved |
| Analog (DLY) | Mix, Time, F.Back, Sync, Trail | Mix, Time, Feedback, Sync, Trail | Low: likely display-label difference |
| COMP4 (PRE) | Sustain, Attack, Volume, Clipping | Sustain, Attack, VOL, Clip | Low: likely display-label abbreviations |

## Consequence for preset generation

The manual validates the available model names and the musical purpose of the
controls. It cannot prove that a particular float in a `.prst` file is the
documented control, because parameter offsets and defaults are absent from the
manual. A known-good Valeton Suite export that changes one setting at a time is
required to confirm each disputed control's binary slot.

Until those captures exist, the generator must retain its catalogue-derived
binary slots. It already normalizes unambiguous spelling differences such as
`Tone_Cut` to the manual label `Tone Cut`; it must not guess a slot for Yellow
OD's missing Tone or B-Boost's conflicting controls.

## Next validation set

Export four presets from Valeton Suite, each differing from a baseline by exactly
one saved change:

1. B-Boost: Gain, Tone, and VOL.
2. Yellow OD: resolved from `67-The Verve.prst`; retain a controlled export if values/ranges need further checking.
3. Analog Delay: Feedback/F.Back.
4. COMP4: Clipping.

Comparing each pair identifies the GP-50's actual parameter float slot and lets
the catalogue be updated without guessing.
