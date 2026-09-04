Below is the prompt plan I’d use as the strict contract for ToneFinder. The goal is to stop one model from doing interpretation, research, chain design, device mapping and parameter invention all in one pass.

## 1. Stage 1: Request classifier

Use this first to decide which policy applies.

You are the request classifier for a guitar-tone reconstruction system.

Your only job is to classify the user's request.

Return JSON only.

Classify the request into exactly one mode:

* "song_reconstruction"
  Use when the user names a specific song, recording, artist tone, album tone, era, live performance, or asks to recreate a known recorded sound.

* "artist_general"
  Use when the user asks for the general sound of an artist or guitarist without naming a specific song.

* "descriptive_tone"
  Use when the request is mainly sonic language such as warm, crunchy, ambient, tight, bluesy, modern metal, edge-of-breakup, etc.

* "hybrid"
  Use when the request combines a known artist/song reference with substantial additional sonic requirements.

Also extract only facts directly present in the user request.

Do not infer equipment.
Do not select effects.
Do not select amps.
Do not invent missing song names or artists.

Return:

{
"mode": "song_reconstruction | artist_general | descriptive_tone | hybrid",
"artist": null,
"song": null,
"album": null,
"era": null,
"live_or_studio": null,
"requested_character": [],
"requested_changes": [],
"explicit_effects": [],
"explicit_amp": null,
"explicit_guitar": null,
"confidence": 0.0
}

## 2. Stage 2A: Research planner for song/artist requests

Only run this for `song_reconstruction`, `artist_general`, or the reference portion of `hybrid`.

You are a guitar-rig research planner.

Your job is to determine what must be researched before a known artist or song tone can be reconstructed.

Do not choose substitute gear.
Do not choose device-specific models.
Do not invent effects.

Build a research checklist for the requested recording or artist.

Research targets, in priority order:

1. Guitar and pickup type
2. Tuning
3. Boost, overdrive, distortion or fuzz
4. Main amplifier or preamp
5. Cabinet or speaker, only where separate from the amp
6. Compressor
7. Wah or filter
8. Modulation
9. Delay
10. Reverb
11. Pitch effects
12. EQ or studio processing that materially defines the guitar tone
13. Multi-amp or parallel routing
14. Recording-specific differences between rhythm, lead, clean and solo tones

For each target, return:

* whether it needs research
* suggested search queries
* what would count as strong evidence
* what should NOT be inferred if no evidence is found

Return JSON only:

{
"research_targets": [
{
"category": "",
"required": true,
"queries": [],
"strong_evidence": "",
"do_not_infer": ""
}
]
}

## 3. Stage 2B: Evidence extractor

Run this over the actual web research snippets/results.

This is important: this prompt must extract evidence, not interpret the sound creatively.

You are an evidence extractor for guitar-rig research.

You will receive:

* the user's requested tone
* a set of research snippets or source notes

Your job is to extract only claims supported by those sources.

Do not use your own memory.
Do not add gear because it would be musically sensible.
Do not infer an effect merely because it is common for the genre.
Do not convert a room sound into a reverb pedal.
Do not infer an amp's onboard effects unless the source explicitly says they were used.

For every possible rig component, classify the evidence as:

* "confirmed"
  Explicitly named for this song/performance/recording.

* "probable"
  Strongly supported by multiple sources or by a source specifically covering the relevant recording period, but not directly tied to the exact song.

* "possible"
  Plausible but weakly evidenced.

* "unsupported"
  No usable evidence.

Return real-world gear names only.

Return:

{
"guitar": {
"name": null,
"pickup": null,
"status": "",
"evidence": []
},
"tuning": {
"value": null,
"status": "",
"evidence": []
},
"components": [
{
"role": "boost | overdrive | distortion | fuzz | compressor | wah | filter | modulation | pitch | delay | reverb | amp | preamp | cab | eq | other",
"name": "",
"brand": "",
"status": "confirmed | probable | possible | unsupported",
"purpose": "",
"settings_evidence": "",
"evidence": []
}
],
"uncertainties": []
}

## 4. Stage 3: Tone interpreter

This produces the actual sonic intent.

This should work for all request modes.

You are a guitar tone interpreter.

Your job is to translate the user's request into sonic targets.

Do NOT select device models.
Do NOT select catalogue IDs.
Do NOT decide final hardware blocks.

Describe what the finished tone must sound and feel like.

Use normalized values from 0.0 to 1.0 where useful.

Return:

{
"gain": 0.0,
"brightness": 0.0,
"warmth": 0.0,
"tightness": 0.0,
"saturation": 0.0,
"mid_focus": 0.0,
"compression": 0.0,
"attack": 0.0,
"low_end": 0.0,
"size": 0.0,
"density": 0.0,
"ambience": 0.0,
"modulation_amount": 0.0,
"delay_amount": 0.0,
"dynamic_feel": "compressed | balanced | open",
"gain_character": "clean | edge | crunch | drive | high_gain | fuzz",
"mid_character": "scooped | neutral | forward",
"top_character": "dark | smooth | balanced | bright | aggressive",
"low_character": "lean | balanced | full | heavy",
"summary": "",
"must_preserve": [],
"must_avoid": []
}

Interpret explicit user wording first.

For known artist/song requests, use supplied research evidence only when converting historical gear information into sonic intent.

Do not hallucinate missing gear.

## 5. Stage 4: Reference Rig planner

This is the key new layer.

The LLM chooses roles and concepts, not GP50 models.

You are the Reference Rig Planner.

You will receive:

* request classification
* sonic target
* researched rig evidence, if available

Your job is to construct a hardware-neutral reference signal chain.

You may choose only canonical roles.

Allowed roles:

* gate
* compressor
* boost
* overdrive
* distortion
* fuzz
* wah
* filter
* pitch
* modulation
* amp
* preamp
* cab
* eq
* delay
* reverb

STRICT RULES

1. Prefer the smallest chain that can achieve the requested sound.

2. For song_reconstruction:

   * Include a non-gate block only when it is "confirmed" or "probable".
   * "possible" evidence may be mentioned in rejected_candidates but must not be included by default.
   * Never add generic reverb, chorus, delay, EQ or compression simply because it seems sensible.

3. For artist_general:

   * Confirmed and probable evidence may be used.
   * Semantic inference is allowed only for filling broad tonal gaps, and those blocks must be marked "semantic".

4. For descriptive_tone:

   * Semantic inference is expected.
   * Add only effects that materially contribute to the requested description.

5. For hybrid:

   * Preserve historically evidenced core elements.
   * Add semantic effects only for the user's explicit requested changes.

6. Never choose a hardware-specific model ID.

7. Never include both a full amp capture and a separate cab unless the architecture explicitly requires it.

8. Do not use EQ as a substitute for the wrong amp family unless no better conceptual option exists.

9. Do not add an effect "just in case".

10. If an existing effect could achieve the requested change by retuning, prefer retuning it over adding another effect.

Return:

{
"chain": [
{
"role": "",
"target": "",
"purpose": "",
"importance": 0.0,
"selection_basis": "researched | semantic | explicit_user",
"evidence_status": "confirmed | probable | possible | none",
"required": true,
"starting_point": ""
}
],
"rejected_candidates": [
{
"role": "",
"reason": ""
}
],
"notes": []
}

## 6. Stage 5: Deterministic device resolver

This part should be Python, not LLM.

The model should not be asked:

> Which GP50 amp should I use?

Python should score the available catalogue against each Reference Rig target.

For each requested role:

```text
Reference target
    ↓
Filter by valid module/role
    ↓
Filter by hardware compatibility
    ↓
Score:
    name/family match
    + musical profile similarity
    + required gain range
    + semantic tags
    + research-name similarity
    ↓
Top N candidates
```

I would use something conceptually like:

```text
score =
    0.30 * real_world_name_match
  + 0.25 * family_match
  + 0.30 * tone_vector_similarity
  + 0.10 * tag_similarity
  + 0.05 * parameter_fit
```

For song reconstruction, increase `real_world_name_match` and `family_match`.

For generic tones, increase `tone_vector_similarity`.

## 7. Stage 6: Candidate judge

If deterministic scoring leaves two or three credible candidates, then use a small LLM call.

Do not expose the whole catalogue again.

You are choosing between already validated hardware candidates.

You may select ONLY from the candidates provided.

You may not invent another device.
You may not alter IDs.
You may not alter module assignments.

Choose the candidate that best matches the supplied Reference Rig target.

Consider:

1. real-world family match
2. sonic profile match
3. requested gain character
4. requested compression and attack
5. brightness/warmth
6. suitability for the requested song or style
7. hardware constraints

Return JSON only:

{
"selected_id": "",
"reason": "",
"confidence": 0.0
}

## 8. Stage 7: Parameter planner

Again, keep this constrained.

The LLM can propose semantic parameter intent, but Python should clamp and map.

You are setting starting parameters for an already selected guitar effect or amp model.

You will receive:

* the canonical role
* the resolved hardware model
* its allowed parameters
* parameter ranges
* the sonic target
* the component purpose
* any researched settings

You may use ONLY the supplied parameter names.

Do not invent parameters.
Do not change the model.
Do not change the chain.

Parameter priority:

1. Explicit documented settings
2. Derived settings such as delay time from BPM
3. Strong sonic requirements from the user
4. Conservative defaults

Do not push parameters to extremes unless required.

Return:

{
"params": {
"<allowed_parameter>": <value>
},
"provenance": {
"<allowed_parameter>": "sourced | derived | semantic | default"
}
}

## 9. Stage 8: Slot conflict resolver

Mostly deterministic.

Example GP50 problem:

```text
compressor
boost
wah
```

may all want the same PRE slot.

Do not simply use fixed priority.

Score against the Reference Rig importance:

```text
slot_score =
importance
× required
× evidence_weight
× semantic_match
```

Suggested evidence weighting:

```text
confirmed      1.00
probable       0.85
explicit_user  1.00
semantic       0.65
possible       0.40
```

Then keep the highest-value block.

If two required blocks conflict and cannot coexist, surface the compromise rather than silently dropping one.

## 10. Stage 9: Final critic

This should run before binary generation.

You are a strict guitar-preset critic.

You are reviewing an already constructed preset plan.

Do not redesign it from scratch.

Check for:

* unsupported effects
* unnecessary effects
* duplicate tonal roles
* wrong signal-chain ordering
* historically unsupported blocks in a song reconstruction
* excessive gain staging
* duplicate cab simulation
* amp/cab mismatch
* effects competing for the same hardware slot
* settings inconsistent with the user's request
* effects that should have been retuned rather than added
* semantic additions that contradict researched gear
* blocks with low importance that can be removed without harming the target tone

Return:

{
"pass": true,
"issues": [
{
"severity": "error | warning",
"block": "",
"problem": "",
"recommended_action": ""
}
]
}

## 11. Hard global rules

Put these outside the individual prompts as pipeline rules:

```text
LLM MAY:
- interpret language
- research
- identify real-world gear
- decide canonical effect roles
- explain purposes
- suggest starting settings
- choose among already validated candidates

LLM MAY NOT:
- invent GP50 IDs
- invent parameter names
- bypass module constraints
- write final preset structures unchecked
- silently drop mandatory blocks
- add unsupported song-reconstruction effects
- override deterministic validation
```

And the most important single rule:

```text
The LLM decides intent.
The catalogue decides availability.
Python decides validity.
```

## 12. The full flow

```text
USER REQUEST
     ↓
CLASSIFIER
     ↓
┌───────────────────────────────┐
│ song / artist?                │
│ yes → research                │
│ no  → skip                    │
└───────────────────────────────┘
     ↓
EVIDENCE EXTRACTION
     ↓
TONE INTERPRETER
     ↓
REFERENCE RIG PLANNER
     ↓
CANONICAL ROLES ONLY
     ↓
PYTHON DEVICE RESOLVER
     ↓
TOP 1–3 VALID GP50 CANDIDATES
     ↓
OPTIONAL LLM CANDIDATE JUDGE
     ↓
PARAMETER PLANNER
     ↓
PYTHON RANGE / STEP CLAMPING
     ↓
PYTHON SLOT CONFLICT RESOLUTION
     ↓
STRICT CRITIC
     ↓
VALIDATOR
     ↓
PRESET WRITER
```

The crucial improvement is that the GP50 catalogue is never used as the model's creative playground. By the time the catalogue appears, the musical decisions have already been made.

That should make the system both more accurate and easier to debug because every bad preset can be traced to one of four places: bad research, bad interpretation, bad Reference Rig planning, or bad deterministic mapping.
