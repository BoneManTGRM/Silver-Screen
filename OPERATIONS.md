# Silver-Screen Operations Runbook

## Service model

Silver-Screen has two execution layers:

1. A deterministic story pipeline that validates a brief, generates structured film state, and runs narrative TGRM repair.
2. A durable AI-video worker that decomposes a target runtime into short provider clips, checkpoints every transition, verifies accepted footage, repairs only failed shots, and resumes until completion or an explicit gate.

The Streamlit app can process bounded batches. The CLI can run one batch or continue a run until the target or a budget gate is reached. Durable storage is required for resume behavior.

## Run lifecycle

| Stage | Manifest state | Meaning |
| --- | --- | --- |
| Created | `running / created / 0` | Workspace and input contract were created |
| Validated | `running / validated / 5` | Brief passed validation |
| Generating | `running / generating / 20` | Deterministic story state is being built |
| Repairing | `running / repairing / 48` | Narrative TGRM is verifying bounded corrections |
| Video production | `running / video_production / 72-89` | Provider clips are submitted, recovered, downloaded, verified, and checkpointed |
| Video checkpoint | `partial / video_checkpoint / <100` | A safe batch ended; accepted clips and prediction IDs are durable |
| Video blocked | `blocked / video_blocked / <100` | A retry, provider-call, spend, or assembly gate stopped the run safely |
| Complete | `complete / complete / 100` | Target runtime was reached and final artifacts were persisted |
| Failed | `failed / failed` | A non-recoverable pipeline error was recorded |

A `partial` run is not a failure. Continue it with the UI or `silver-screen resume-video <run-id>`.

## Durable video contract

Every AI-video run stores these files under `runs/<run-id>/media/`:

```text
video_queue.json         complete shot queue, provider IDs, attempts, verification, events
video_runtime.json       compact status, metrics, MSIL, stop reason, artifacts
video_scar_memory.json   successful TGRM video repair patterns
clips/                   verified generated MP4 clips
continuity/              accepted final frames used to chain later clips
chapters/                assembled chapter reels
partial_ai_film.mp4      verified assembly at an incomplete checkpoint
final_ai_film.mp4        verified assembly when the runtime target is reached
```

Writes are atomic. A provider prediction ID is persisted immediately after submission so an interrupted process can poll the existing prediction instead of silently paying for a duplicate request.

## Required configuration

Set `REPLICATE_API_TOKEN` in deployment secrets. Do not commit it.

Recommended starting values:

```text
SILVER_SCREEN_VIDEO_MODEL=google/veo-3.1-fast
SILVER_SCREEN_VIDEO_DURATION=8
SILVER_SCREEN_TARGET_RUNTIME_SECONDS=60
SILVER_SCREEN_VIDEO_MAX_SHOTS=128
SILVER_SCREEN_VIDEO_BATCH_SIZE=2
SILVER_SCREEN_VIDEO_MAX_RETRIES=2
SILVER_SCREEN_VIDEO_MAX_PROVIDER_CALLS=0
SILVER_SCREEN_VIDEO_MAX_SPEND_USD=0
SILVER_SCREEN_VIDEO_COST_PER_SECOND_USD=0
SILVER_SCREEN_VIDEO_CONTINUITY=1
SILVER_SCREEN_VIDEO_CHAPTER_SIZE=12
```

Zero spend values disable estimated-spend gating. Silver-Screen does not guess provider prices; enter a current cost-per-generated-second value when using a spend limit.

## Production commands

Start a checkpointed one-minute production:

```bash
silver-screen run \
  --brief examples/brief.json \
  --media ai-video \
  --target-runtime-seconds 60 \
  --video-batch-size 2 \
  --video-max-retries 2
```

List resumable runs:

```bash
silver-screen list-resumable
```

Inspect one run:

```bash
silver-screen video-status <run-id> --json
```

Continue the next bounded batch:

```bash
silver-screen resume-video <run-id> --video-batch-size 2
```

Continue until completion or a gate:

```bash
silver-screen resume-video <run-id> --video-continuous
```

## Reparodynamics and TGRM behavior

For every incomplete shot, the worker applies:

```text
PLAN       map target runtime to ordered shot segments
GENERATE   submit one model prediction and persist its ID
DETECT     identify provider, download, container, duration, continuity, or budget fractures
REPAIR     modify only the affected shot's prompt, seed, audio setting, or retry path
VERIFY     accept only a durable MP4 with a valid container and usable duration
STABILIZE  checkpoint the queue, metrics, prediction state, and accepted footage
REINFORCE  store successful repair strategy and seed in video scar memory
CONTINUE   advance to the next incomplete shot
```

Video RYE is verified usable seconds divided by bounded production energy. Video MSIL combines completion, continuity coverage, failure rate, and repair oscillation.

## Budget gates

Use both of these in paid deployments:

- `--video-max-provider-calls` limits all prediction attempts across the production.
- `--video-max-spend-usd` and `--video-cost-per-second-usd` create an estimated spend gate.

A gate produces a `blocked` checkpoint, not data loss. Raise the approved limit and resume the same run.

## Recovery procedures

### Interrupted during provider processing

Run `video-status`. If the shot has a persisted prediction ID, `resume-video` polls that existing prediction before creating a new request.

### Failed shot

The worker records the failure, chooses a minimal TGRM repair, and retries only that shot within the configured retry budget. Accepted earlier clips remain unchanged.

### Missing or corrupt accepted clip

Resume reconciliation reopens that shot as pending. It is regenerated without deleting other verified clips.

### Assembly failure

Individual verified clips and chapter reels remain durable. Correct FFmpeg or storage problems and resume; the worker reassembles from accepted footage.

### Provider-call or spend gate

Review `video_runtime.json`, increase only the approved limit, and resume. Do not edit shot statuses manually.

## Deployment

1. Use Python 3.10+ or the supplied container.
2. Mount `./runs` to durable storage. Ephemeral storage defeats resume guarantees.
3. Keep `SILVER_SCREEN_DEBUG=0` in production.
4. Put authentication, TLS, request limits, and audit logging at the platform or reverse-proxy layer.
5. Prefer small Streamlit batches. Use an isolated worker or CLI process for continuous long productions.
6. Monitor storage and provider spend.
7. Back up complete run directories or final ZIP bundles.

```bash
docker compose up --build -d
docker compose logs -f silver-screen
curl -f http://127.0.0.1:8501/_stcore/health
```

## Verification

```bash
python -m compileall -q silver_screen streamlit_app.py
python -m pytest -q
python -m silver_screen health --json
make smoke
```

## Retention

- Never delete a run while its manifest says `running`.
- Preserve partial and blocked queues until the operator intentionally abandons them.
- Keep failed manifests for audit.
- Remove expanded clips only after final bundle integrity and retention requirements are satisfied.

## Security boundaries

- Treat uploaded media as untrusted input.
- Process only media the operator is authorized to use.
- Never put provider tokens in briefs, manifests, screenshots, issues, or source control.
- The run root remains fixed to the allowlisted `./runs` directory.
- Uploaded code is never executed.
- Voice files are inventoried but not cloned or synthesized in this release.
