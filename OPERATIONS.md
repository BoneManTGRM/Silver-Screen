# Silver-Screen Operations Runbook

## Service model

The current service executes one production request synchronously inside the Streamlit process or CLI process. Each run is isolated in a unique workspace. No background worker, distributed queue, external database, or remote model is required.

This model is appropriate for local use, demonstrations, controlled internal deployments, and low-concurrency production. A future multi-user deployment should put the existing pipeline behind a job queue and object storage while preserving the run manifest contract.

## Run lifecycle

| Stage | Manifest state | Meaning |
| --- | --- | --- |
| Created | `running / created / 0` | Workspace exists and input/options were recorded |
| Validated | `running / validated / 5` | Brief passed normalization and safety checks |
| Generating | `running / generating / 20` | Initial deterministic film state is being built |
| Repairing | `running / repairing / 48` | TGRM is detecting and verifying bounded corrections |
| Rendering | `running / rendering_media / 72` | Optional cards or preview videos are being produced |
| Persisting | `running / persisting / 90` | JSON, text, manifest, and ZIP artifacts are being written |
| Complete | `complete / complete / 100` | Final result and bundle are durable |
| Failed | `failed / failed` | A core stage failed and the error is recorded |

Media errors do not fail the core run. They are added to `warnings`, and PNG or screenplay outputs are retained whenever possible.

## Production deployment

1. Use the supplied Docker image or Python 3.10 and later.
2. Mount the fixed `./runs` directory to durable storage. `SILVER_SCREEN_RUNS_DIR` is an allowlisted alias and must remain `runs`.
3. Keep `SILVER_SCREEN_DEBUG=0` outside a controlled diagnostic session.
4. Put TLS, authentication, request limits, and audit logging in the reverse proxy or platform layer.
5. Back up completed run directories or copy final ZIP bundles to object storage.
6. Monitor disk usage because film JSON, uploaded portraits, and video previews can accumulate.

Example:

```bash
docker compose up --build -d
docker compose logs -f silver-screen
curl -f http://127.0.0.1:8501/_stcore/health
```

## Health diagnostics

```bash
python -m silver_screen health --json
```

Critical checks:

- Core package imports.
- Run root can be created and written.

Degradable checks:

- Streamlit availability.
- Pillow availability for PNG cards.
- NumPy, MoviePy, and FFmpeg availability for preview video.

A degraded result can still be operational for CLI screenplay generation.

## Failure recovery

### Core run failure

1. Locate the run by ID under the fixed `./runs` workspace root.
2. Read `manifest.json` and inspect `stage`, `error`, `brief`, and `options`.
3. Correct the environment or input.
4. Replay the normalized `brief` with the recorded seed.
5. Do not edit a failed workspace into a complete workspace. Start a new run so provenance remains intact.

### Media failure

1. Confirm `screenplay.txt`, `film.json`, and `tgrm.json` exist.
2. Read `media.error` and `warnings` in `result.json`.
3. Run `python -m silver_screen health --json`.
4. Install FFmpeg only when video is required. PNG card mode needs Pillow but not FFmpeg.
5. Re-run with `--media cards` to avoid a codec dependency.

### Interrupted process

A process killed between atomic writes can leave a run in `running` state, but completed files should not contain partial JSON or text. Treat a stale `running` manifest as interrupted, preserve it for audit, and start a new run.

## Retention

A conservative policy is:

- Keep failed and interrupted manifests for 30 days.
- Keep final ZIP bundles according to project policy.
- Remove redundant expanded media only after bundle integrity is verified.
- Never delete a run while it is `running`.

ZIP integrity check:

```bash
python - <<'PY'
from pathlib import Path
from zipfile import ZipFile

for path in Path("runs").glob("*/*.zip"):
    with ZipFile(path) as archive:
        print(path, "OK" if archive.testzip() is None else "CORRUPT")
PY
```

## Concurrency and scaling

The run ID and workspace design already permits independent concurrent processes when they share a filesystem that supports atomic rename. For a larger deployment:

1. Move request intake to an authenticated API.
2. Store the normalized brief and options in a durable queue.
3. Execute `run_pipeline` in isolated workers.
4. Replace local workspace storage with object storage or a mounted volume.
5. Emit progress events from the existing callback to a database or event stream.
6. Add per-tenant authorization before exposing run history or artifacts.
7. Keep TGRM deterministic and version every manifest schema.

## Security boundaries

- Treat uploaded media as untrusted input.
- Keep platform upload limits in addition to the application read limit.
- Do not put API keys or secrets in briefs, manifests, or repository files.
- Run storage is fixed to the allowlisted `./runs` directory; user input never becomes a storage path.
- This release does not execute uploaded code.
- This release does not clone or synthesize voices.
- Only use images and audio that the operator is authorized to process.
