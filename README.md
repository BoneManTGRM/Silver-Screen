# Silver-Screen

**A Reparodynamics production system using TGRM, RYE, and MSIL**

Silver-Screen turns a story brief into a deterministic screenplay package and a resumable AI-film production. It plans a target runtime as an ordered shot queue, submits real model-backed video generations, persists prediction IDs, verifies MP4 outputs, repairs only failed shots, chains accepted final frames for continuity, and assembles verified footage chapter by chapter.

Founder: **Cody Ryan Jenkins** ([BoneManTGRM](https://github.com/BoneManTGRM), [@Reparodynamics](https://x.com/Reparodynamics))

## What is operational

- Validated, normalized production briefs with deterministic seeds.
- Story bible, cast, acts, chapters, scenes, shots, continuity anchors, and screenplay blueprint.
- Narrative TGRM with energy budgets, verification, rollback, stop reasons, and scar memory.
- Actual AI-generated video through Replicate official model endpoints.
- Target-runtime planning rather than a fixed eight-clip ceiling.
- Durable shot queues and immediate provider-prediction checkpoints.
- Resume support without regenerating already verified clips.
- Shot-level TGRM repair for provider, download, container, duration, and assembly failures.
- Final-frame continuity chaining between accepted clips.
- Provider-call and estimated-spend gates.
- Video RYE, video MSIL, continuity coverage, failure rate, repair oscillation, and stop reasons.
- Verified partial assemblies, chapter reels, and final MP4 assembly.
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
  continuity gaps, exhausted retries, assembly failure, or a budget gate.

MINIMAL CORRECTION
  Repair only the affected shot through a bounded retry, prompt simplification,
  seed change, audio change, redownload, or verified-container regeneration.

VERIFY
  Accept only a durable MP4 with a valid container and usable duration.

STABILIZE
  Atomically save the queue, runtime metrics, artifacts, provider state, and events.

REINFORCE
  Record successful repair strategies and seeds in video scar memory.

CONTINUE
  Advance to the next incomplete shot until the runtime target or an explicit gate.
```

A normal bounded batch ends as `partial`, not failed. Continue the same run from the UI or CLI. A budget or retry gate ends as `blocked` while preserving all accepted footage.

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

Long-production settings:

```text
SILVER_SCREEN_TARGET_RUNTIME_SECONDS=60
SILVER_SCREEN_VIDEO_MAX_SHOTS=128
SILVER_SCREEN_VIDEO_BATCH_SIZE=4
SILVER_SCREEN_VIDEO_MAX_RETRIES=2
SILVER_SCREEN_VIDEO_MAX_PROVIDER_CALLS=0
SILVER_SCREEN_VIDEO_MAX_SPEND_USD=0
SILVER_SCREEN_VIDEO_COST_PER_SECOND_USD=0
SILVER_SCREEN_VIDEO_CONTINUITY=1
SILVER_SCREEN_VIDEO_CHAPTER_SIZE=12
```

Zero spend values disable estimated-spend gating. Silver-Screen does not hard-code or guess provider pricing; supply a current cost-per-generated-second value when using the spend gate.

## Quick start

### Streamlit studio

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

The studio provides:

- target runtime up to 90 minutes
- planned paid-clip count
- checkpoint batch size
- TGRM retry budget
- provider-call budget
- estimated-spend gate
- final-frame continuity control
- continuous or checkpointed execution
- resumable-run selector
- production queue, TGRM audit, video metrics, partial assembly, and downloads

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

Each persisted run is isolated:

```text
runs/
  <run-id>/
    manifest.json
    brief.json
    film.json
    outline.json
    screenplay.txt
    tgrm.json
    result.json
    media/
      video_queue.json
      video_runtime.json
      video_scar_memory.json
      clips/
        shot_0001.mp4
        shot_0002.mp4
      continuity/
        shot_0001_last.jpg
      chapters/
        chapter_001.mp4
      partial_ai_film.mp4
      final_ai_film.mp4
    <title>-<run-id>.zip
```

The provider prediction ID is saved immediately after submission. When a process restarts, Silver-Screen polls that prediction before creating another paid request. Accepted clips are reconciled and retained; a missing or corrupt accepted file reopens only that shot.

## Video metrics

Silver-Screen records:

- planned, verified, and failed shot counts
- verified runtime
- provider calls and repair count
- completion ratio
- continuity coverage
- estimated spend
- video energy
- video RYE
- video MSIL stability, failure rate, oscillation, and collapse risk

The current operational definition is:

```text
Video RYE = verified usable seconds / bounded production energy
```

Production energy includes provider attempts, repair operations, and verified accepted shots. It is an internal system metric, not an established physical or economic measurement.

## Budget and safety gates

For paid deployments, use:

```bash
--video-max-provider-calls 30
--video-cost-per-second-usd <current-provider-rate>
--video-max-spend-usd 100
```

The call budget covers the whole queue, including repair attempts. A gate creates a durable `blocked` checkpoint. Increase only an approved limit and resume the existing run.

Uploaded reference images must be authorized by the operator. Voice files are inventoried only; this release does not clone or synthesize voices.

## Architecture

```text
streamlit_app.py              long-running interactive studio
silver_screen/
  science.py                  Reparodynamics vocabulary and format contracts
  script_engine.py            deterministic story and screenplay state
  tgrm.py                     narrative detect, repair, verify, rollback, reinforce
  video_runtime.py            durable queue, video RYE/MSIL, fractures, scars, gates
  ai_video.py                 provider predictions, resume, verification, continuity, assembly
  media.py                    AI-video, cards, and honest local-preview routing
  runtime.py                  atomic run manifests, reopen, artifacts, bundles, history
  pipeline.py                 end-to-end orchestration and resume API
  health.py                   capability and storage diagnostics
  cli.py                      run, resume, status, health, and history commands
tests/                        regression and queue-recovery coverage
```

## Deployment

The supplied container includes FFmpeg and writes to the fixed `./runs` directory. Mount that directory to durable storage:

```bash
docker compose up --build
```

For long productions, prefer small Streamlit batches or an isolated CLI/worker process. Ephemeral storage removes the ability to resume after a restart.

See [OPERATIONS.md](OPERATIONS.md) for recovery, retention, budget, and deployment procedures.

## Verification

```bash
python -m compileall -q silver_screen streamlit_app.py
python -m pytest -q
python -m silver_screen health --json
make smoke
```

The suite covers story validation, deterministic replay, all story formats, narrative TGRM, durable bundles, filesystem safety, AI-video provider contracts, runtime planning, queue extension, checkpoint/resume, targeted shot repair, scar memory, provider-call gates, and durable video status files.

## Scope and realism

Silver-Screen can orchestrate production for a long target runtime, but the final quality, total cost, and elapsed time depend on the configured video model, provider capacity, retry rate, continuity performance, and operator review. A 90-minute target can require hundreds of paid short-clip generations. The system therefore exposes hard shot, call, retry, and spend gates rather than claiming that feature-length generation is free or immediate.

## Science references and provenance

- TGRM: [10.5281/zenodo.17273433](https://doi.org/10.5281/zenodo.17273433)
- Five-law system: [10.5281/zenodo.17538091](https://doi.org/10.5281/zenodo.17538091)
- Coding velocity simulation: [10.5281/zenodo.17336075](https://doi.org/10.5281/zenodo.17336075)
- Corpus: https://bonemantgrm.github.io/reparodynamics-corpus/

Reparodynamics is presented as Cody Ryan Jenkins's conceptual engineering framework. The software metrics are operational heuristics for this system, not established clinical or physical measurements.

## License

No new license is implied by this release. Use the repository's existing licensing terms and attribution requirements.
