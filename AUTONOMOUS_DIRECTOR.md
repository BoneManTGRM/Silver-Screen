# Silver-Screen 9.0 Autonomous Director

Silver-Screen 9.0 turns the existing durable video pipeline into a model-independent production supervisor. It does not claim to train a better foundational video model. Its advantage is the closed loop around the models: persistent memory, approved shot contracts, routing, verification, bounded retakes, candidate selection, timeline locks, evidence, and delivery.

## Defining workflow

```text
CONCEPT AND SCRIPT
  Build several provider-free screenplay/shot plans and select the strongest.

PRODUCTION MEMORY
  Create or load a persistent world graph for characters, wardrobe, locations,
  props, vehicles, chronology, relationships, visual rules, and repair scars.

BINDING PREVISUALIZATION
  Build the complete shot ledger, model-routing plan, storyboard, and local
  animatic before paid generation.

SINGLE BUDGET AUTHORIZATION
  Approve one provider-call and optional estimated-spend ceiling for the selected plan.

GENERATE
  Route each shot to the configured specialist model or the active general model.

VERIFY
  Check MP4 integrity, visual quality, semantic shot-contract compliance,
  transition quality, world consistency, and approved prompt hashes.

MINIMAL REPAIR
  Preserve the accepted candidate, reopen only the failed shot, and append a
  targeted repair directive for the failed dimensions.

COMPARE
  Score the replacement against the preserved candidate. Keep the new version
  only when it produces a measured gain; otherwise restore the accepted original.

FINISH
  Rebuild cinematic transitions, optionally add voices, create a normalized
  delivery master, and emit a project-level evidence report.
```

## Persistent memory

Project memory is stored separately from an individual run:

```text
runs/
  _projects/
    <project-id>/
      production_memory.json
      versions/
        production_memory_v0001.json
        production_memory_v0002.json
```

Each run also receives a frozen memory snapshot and prompt-core hash. Later decisions and repair scars can be appended without silently changing the approved visual world contract.

Memory can include:

- character identity and performance contracts
- wardrobe states and accessories
- locations, geography, entrances, weather, time, and damage
- hero props, ownership, position, and condition
- vehicles and persistent appearance
- relationships and chronology
- story facts that later shots may not contradict
- visual medium, palette, lens language, and camera grammar
- operator decisions and successful repair scars

Memory is production metadata. It does not identify people and does not create biometric face embeddings.

## Semantic review

The local semantic fallback is intentionally conservative. It verifies that a shot has a complete, auditable contract, but it does not claim to understand visual content.

When `OPENAI_API_KEY` and semantic review are enabled, a small set of sampled frames can be compared against the approved shot contract through OpenAI's multimodal Responses API. The reviewer is instructed not to identify real people or infer sensitive traits. It evaluates only visible production requirements such as action, performance, composition, wardrobe, props, continuity, and contradictions.

```text
SILVER_SCREEN_SEMANTIC_REVIEW=1
SILVER_SCREEN_SEMANTIC_PROVIDER=1
SILVER_SCREEN_SEMANTIC_MODEL=gpt-5-mini
SILVER_SCREEN_SEMANTIC_ACCEPT_SCORE=0.76
SILVER_SCREEN_SEMANTIC_HARD_REJECT_SCORE=0.46
SILVER_SCREEN_SEMANTIC_SAMPLE_FRAMES=4
SILVER_SCREEN_SEMANTIC_GATE=0
```

`SILVER_SCREEN_SEMANTIC_GATE=0` keeps provider semantic findings advisory. The Autonomous Director can still use them to prioritize a consented retake. Set the gate to `1` only after calibrating the reviewer against real productions.

## Model routing

The routing registry supports categories such as:

- general
- performance
- action
- environment
- dialogue
- animation
- localized repair
- upscaling
- lip synchronization

Unconfigured categories fall back to `SILVER_SCREEN_VIDEO_MODEL`. This makes routing safe to enable before specialist providers are added.

## Candidate selection

Every autonomous retake archives the accepted version first. Selection uses available visual, semantic, and incoming-transition evidence. A replacement must exceed the preserved candidate by the configured minimum gain.

```text
SILVER_SCREEN_CANDIDATE_MAX_RETAKES_PER_SHOT=3
SILVER_SCREEN_CANDIDATE_MIN_GAIN=0.015
```

## Timeline editor

The local timeline editor can:

- reorder verified shots
- trim heads and tails
- choose fade or fade-to-black joins
- adjust overlap duration
- lock approved shots against autonomous retakes
- annotate editor decisions
- render a new local cut
- export JSON and CSV edit-decision lists

This is a practical non-linear editing foundation, not yet a full drag-and-drop browser NLE with layered waveforms and compositing.

## Delivery and evidence

The Autonomous Director creates:

```text
autonomous_director.json
autonomous_evidence_report.json
autonomous_evidence_report.html
preproduction/autonomous_preproduction.json
preproduction/model_routing_plan.json
preproduction/animatic/animatic_manifest.json
preproduction/animatic/storyboard.html
preproduction/animatic/director_animatic.mp4
media/semantic_shot_report.json
media/editor_timeline.json
media/editor_timeline_edl.json
media/editor_timeline_edl.csv
media/final_delivery_master_1080p.mp4
```

The project quality score is based on the actual production state: completion, visual evidence, semantic evidence, transitions, and preproduction quality. Its confidence is reduced when semantic review is only provisional.

## Operational limitations

- Foundational video quality remains probabilistic.
- CI does not make paid Replicate, OpenAI, or ElevenLabs calls.
- Provider-specific localized video editing and true viseme-level lip synchronization require additional model adapters.
- Long autonomous runs remain dependent on durable storage and provider capacity.
- Browser-hosted Streamlit sessions may end; the durable run and Autonomous Director state can be continued from the saved checkpoint.
