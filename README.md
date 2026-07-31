# Silver-Screen

**A Reparodynamics production system using TGRM, RYE, and MSIL**

Silver-Screen is a model-independent autonomous film-production supervisor. It turns a story brief or authored screenplay into an approved production plan, persistent world memory, a resumable shot queue, verified generated footage, bounded candidate repairs, cinematic transitions, optional voices, an editable local timeline, and an evidence-backed delivery master.

Founder: **Cody Ryan Jenkins** ([BoneManTGRM](https://github.com/BoneManTGRM), [@Reparodynamics](https://x.com/Reparodynamics))

## Silver-Screen 9.0

The defining workflow is now **Autonomous Director**:

```text
Screenplay and references
  → persistent Production World Graph
  → provider-free planning attempts
  → approved shot ledger and animatic
  → one bounded provider-budget authorization
  → per-shot model routing
  → generation and durable checkpoints
  → visual and semantic shot verification
  → preserved-candidate retakes
  → measured keep or rollback selection
  → cinematic transition finishing
  → optional voice finishing
  → local timeline edit
  → normalized delivery master
  → project-level evidence report
```

Key 9.0 capabilities:

- Persistent project memory across shots, episodes, sequels, retakes, and model changes.
- Characters, wardrobe, locations, props, vehicles, relationships, chronology, story rules, visual style, decisions, and repair scars.
- Provider-free multi-attempt screenplay and shot-plan selection.
- Complete storyboard and local animatic before paid generation.
- Model-routing recommendations with safe fallback to the configured general model.
- Local technical visual inspection plus optional OpenAI multimodal semantic contract review.
- Honest provisional semantic reports when no multimodal reviewer is configured.
- Candidate preservation before every autonomous retake.
- Measured comparison that restores the previous clip unless the replacement improves.
- Timeline locks that keep approved footage out of later autonomous retakes.
- Local non-linear editing: reorder, trim, annotate, set transition handles, render, and export EDL files.
- Project-level quality score and confidence based on actual production evidence.
- 1080p, 4K, or source-resolution delivery mastering with optional loudness normalization.

Open **Autonomous Director** in the Streamlit page menu for the guided workflow. See [AUTONOMOUS_DIRECTOR.md](AUTONOMOUS_DIRECTOR.md) for architecture, memory, semantic-review, cost, and operational details.

## What is operational

- Validated, normalized production briefs with deterministic seeds.
- Story bible, cast, acts, chapters, scenes, shots, continuity anchors, and screenplay blueprint.
- Narrative TGRM with energy budgets, verification, rollback, stop reasons, and scar memory.
- Actual AI-generated video through Replicate official model endpoints.
- Target-runtime planning rather than a fixed eight-clip ceiling.
- Durable shot queues and immediate provider-prediction checkpoints.
- Resume support without regenerating already verified clips.
- Shot-level TGRM repair for provider, download, container, duration, assembly, quality, and semantic failures.
- Final-frame continuity chaining between accepted clips.
- Provider-call and estimated-spend gates.
- Video RYE, video MSIL, continuity coverage, failure rate, repair oscillation, and stop reasons.
- Visual Quality and Identity Supervisor with non-biometric broad reference consistency.
- Creative Director, Shot Director, approved prompt ledgers, and anti-cliche gates.
- Professional Script Sync, OpenAI/ElevenLabs/manual voice options, captions, and audio assembly.
- Cinematic Continuity, Director Review, adaptive transitions, and targeted retakes.
- Verified partial assemblies, chapter reels, editor cuts, and final MP4 masters.
- Streamlit controls plus headless CLI run, status, resume, health, and history operations.
- Docker, Compose, non-root execution, durable storage, tests, CI, CodeQL, and dependency review.

## How long-running generation works

Video models create short clips. Silver-Screen creates a longer film by applying the Reparodynamics loop to an ordered queue:

```text
PLAN
  Convert the target runtime into ordered shot segments.

GENERATE
  Submit the next incomplete shot and immediately persist its prediction ID.

DETECT
  Identify provider failure, timeout, missing output, invalid MP4, short duration,
  semantic mismatch, visual-quality failure, continuity gap, exhausted retry,
  assembly failure, or a budget gate.

MINIMAL CORRECTION
  Preserve accepted work and repair only the affected unit through a bounded retry,
  prompt correction, candidate comparison, seed change, audio change, redownload,
  verified-container regeneration, or local timeline/transition edit.

VERIFY
  Accept only a durable MP4 with a valid container, usable duration, and configured
  visual, semantic, continuity, memory, and prompt-contract evidence.

STABILIZE
  Atomically save the queue, runtime metrics, project memory, artifacts, provider
  state, model route, candidate history, evidence, and events.

REINFORCE
  Record successful repair strategies and decisions in video and project scar memory.

CONTINUE
  Advance until the runtime and quality target or an explicit provider, retry,
  storage, call, or spend gate.
```

A normal bounded batch ends as `partial`, not failed. Continue the same run from the UI or CLI. A budget or retry gate ends as `blocked` while preserving accepted footage.

## Required provider configuration

Add the token to deployment secrets, not source control:

```text
REPLICATE_API_TOKEN=r8_your_private_token
```

Default model and video settings:

```text
SILVER_SCREEN_VIDEO_MODEL=google/veo-3.1-fast
SILVER_SCREEN_VIDEO_DURATION=8
SILVER_SCREEN_VIDEO_RESOLUTION=720p
SILVER_SCREEN_VIDEO_ASPECT_RATIO=16:9
SILVER_SCREEN_VIDEO_AUDIO=1
```

Optional semantic shot review:

```text
OPENAI_API_KEY=your_private_key
SILVER_SCREEN_SEMANTIC_REVIEW=1
SILVER_SCREEN_SEMANTIC_PROVIDER=1
SILVER_SCREEN_SEMANTIC_MODEL=gpt-5-mini
SILVER_SCREEN_SEMANTIC_GATE=0
```

The semantic gate is advisory by default. Calibrate it on real productions before allowing automatic rejection.

Long-production settings:

```text
SILVER_SCREEN_TARGET_RUNTIME_SECONDS=60
SILVER_SCREEN_VIDEO_MAX_SHOTS=128
SILVER_SCREEN_VIDEO_BATCH_SIZE=1
SILVER_SCREEN_VIDEO_MAX_RETRIES=2
SILVER_SCREEN_VIDEO_MAX_PROVIDER_CALLS=0
SILVER_SCREEN_VIDEO_MAX_SPEND_USD=0
SILVER_SCREEN_VIDEO_COST_PER_SECOND_USD=0
SILVER_SCREEN_VIDEO_CONTINUITY=1
SILVER_SCREEN_VIDEO_CHAPTER_SIZE=12
```

Zero spend values disable estimated-spend gating. Silver-Screen does not hard-code or guess provider pricing; supply a current cost-per-generated-second value when using the spend gate.

See [.env.autonomous.example](.env.autonomous.example) for the complete 9.0 configuration surface.

## Quick start

### Streamlit studio

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Recommended page order:

1. **Autonomous Director** for a complete guided production.
2. **Production Memory** to inspect or edit a persistent world graph.
3. **Timeline Editor** to reorder, trim, lock, and render accepted footage.
4. **Professional Script Sync** or **Voice Studio** for detailed dialogue work.
5. **Visual Quality Supervisor**, **Director Review**, or **Cinematic Continuity** for specialist review.

### Command line

Start a checkpointed one-minute AI film:

```bash
silver-screen run \
  --brief examples/brief.json \
  --media ai-video \
  --target-runtime-seconds 60 \
  --video-batch-size 2 \
  --video-max-retries 2
```

List resumable productions:

```bash
silver-screen list-resumable
```

Inspect a queue:

```bash
silver-screen video-status <run-id> --json
```

Continue the next batch:

```bash
silver-screen resume-video <run-id> --video-batch-size 2
```

Continue until completion or a gate:

```bash
silver-screen resume-video <run-id> --video-continuous
```

Generate only the deterministic screenplay package:

```bash
silver-screen run \
  --premise "A courier discovers that every delivered letter changes yesterday." \
  --genre thriller \
  --format episode \
  --media off \
  --no-persist \
  --json
```

## Durable workspace

Each persisted run is isolated, while project memory can outlive one run:

```text
runs/
  _projects/
    <project-id>/
      production_memory.json
      versions/
  <run-id>/
    manifest.json
    brief.json
    film.json
    outline.json
    screenplay.txt
    tgrm.json
    result.json
    autonomous_director.json
    autonomous_evidence_report.json
    autonomous_evidence_report.html
    production_memory.json
    production_world_graph.json
    preproduction/
      autonomous_preproduction.json
      model_routing_plan.json
      animatic/
        animatic_manifest.json
        storyboard.html
        director_animatic.mp4
    media/
      video_queue.json
      video_runtime.json
      video_scar_memory.json
      semantic_shot_report.json
      transition_plan.json
      editor_timeline.json
      editor_timeline_edl.json
      editor_timeline_edl.csv
      clips/
      candidates/
      continuity/
      chapters/
      final_delivery_master_1080p.mp4
```

The provider prediction ID is saved immediately after submission. When a process restarts, Silver-Screen polls that prediction before creating another paid request. Accepted clips are reconciled and retained; a missing or corrupt accepted file reopens only that shot.

## Video and project metrics

Silver-Screen records:

- planned, verified, and failed shot counts
- verified runtime
- provider calls and repair count
- completion ratio
- continuity coverage
- visual, semantic, transition, and project quality
- semantic evidence quality and confidence
- model-routing decisions
- candidate gain or rollback
- estimated spend
- video energy, RYE, and MSIL
- production-memory version and prompt-core hash

The current operational definition is:

```text
Video RYE = verified usable seconds / bounded production energy
```

Production energy includes provider attempts, repair operations, and verified accepted shots. It is an internal system metric, not an established physical or economic measurement.

## Budget and authorization gates

For paid deployments, use explicit call and spend ceilings. The Autonomous Director replaces multiple creative confirmations with one plan-specific provider-budget authorization, but it does not silently remove cost limits or provider policies.

```bash
--video-max-provider-calls 30
--video-cost-per-second-usd <current-provider-rate>
--video-max-spend-usd 100
```

The call budget covers the whole queue, including repair attempts. A gate creates a durable `blocked` checkpoint. Increase only an approved limit and resume the existing run.

Uploaded real-person and character references must be authorized by the operator. Provider content rules remain authoritative.

## Architecture

```text
streamlit_app.py                       general landing page
pages/9_Autonomous_Director.py         guided autonomous production
pages/10_Timeline_Editor.py            local non-linear editor
pages/11_Production_Memory.py           persistent world-graph manager
silver_screen/
  autonomous_director.py               closed-loop orchestration and evidence
  autonomous_config.py                 quality/cost profiles
  production_memory.py                 durable world graph and long memory
  production_memory_install.py         memory integration and prompt contracts
  model_routing.py                     shot classification and route planning
  model_routing_runtime.py             safe per-shot route activation
  semantic_supervisor.py               optional multimodal contract verification
  candidate_selection.py               preserve, compare, keep, or roll back
  previsualization.py                   storyboard and local animatic
  timeline_editor.py                   trims, ordering, locks, transitions, EDL
  delivery_master.py                   normalized final delivery encodes
  science.py                            Reparodynamics vocabulary and formats
  script_engine.py                      deterministic story and screenplay state
  tgrm.py                               detect, repair, verify, rollback, reinforce
  video_runtime.py                      durable queue, RYE/MSIL, fractures, gates
  ai_video.py                           provider predictions and recovery
  transition_engine.py                  adaptive local cinematic assembly
  runtime.py                            atomic artifacts, bundles, and history
```

## Deployment

The supplied container includes FFmpeg and writes to the fixed `./runs` directory. Mount that directory to durable storage:

```bash
docker compose up --build
```

For long productions, durable storage is mandatory. Hosted browser sessions may end; the queue, provider prediction, project memory, candidate archive, and Autonomous Director state can be resumed.

See [OPERATIONS.md](OPERATIONS.md) for recovery, retention, budget, and deployment procedures.

## Verification

```bash
python -m compileall -q silver_screen streamlit_app.py
python -m pytest -q
python -m silver_screen health --json
make smoke
```

CI intentionally has no private provider keys and therefore does not make paid Replicate, OpenAI, or ElevenLabs requests. It verifies deterministic planning, prompt and memory contracts, queue recovery, local media processing, candidate preservation, semantic fallback honesty, timeline logic, health, frontend generation, dependencies, and CodeQL.

## Scope and realism

Silver-Screen can supervise a long target runtime, but foundational video quality, total cost, and elapsed time still depend on the selected models, provider capacity, retry rate, semantic-review calibration, and operator standards. Provider-specific localized editing and true viseme-level lip synchronization remain adapter-level future work. The system exposes hard call, retry, storage, and spend gates rather than claiming feature-length generation is free, instant, or guaranteed.

## Science references and provenance

- TGRM: [10.5281/zenodo.17273433](https://doi.org/10.5281/zenodo.17273433)
- Five-law system: [10.5281/zenodo.17538091](https://doi.org/10.5281/zenodo.17538091)
- Coding velocity simulation: [10.5281/zenodo.17336075](https://doi.org/10.5281/zenodo.17336075)
- Corpus: https://bonemantgrm.github.io/reparodynamics-corpus/

Reparodynamics is presented as Cody Ryan Jenkins's conceptual engineering framework. The software metrics are operational heuristics for this system, not established clinical or physical measurements.

## License

No new license is implied by this release. Use the repository's existing licensing terms and attribution requirements.
