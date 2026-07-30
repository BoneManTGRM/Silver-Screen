# Silver-Screen

**A Reparodynamics production system using TGRM, RYE, and MSIL**

Silver-Screen turns a story brief into a deterministic production package: story bible, cast, acts, chapters, scenes, shot plan, screenplay blueprint, TGRM repair audit, optional media previews, and a durable ZIP bundle.

The primary runtime is Python and Streamlit. It is designed to run locally, in CI, or in a container without depending on a remote model. The same normalized brief and seed produce the same initial film state, so repair decisions are reproducible and testable.

Founder: **Cody Ryan Jenkins** ([BoneManTGRM](https://github.com/BoneManTGRM), [@Reparodynamics](https://x.com/Reparodynamics))

## What is operational now

1. **Validated briefs** with normalized genre, tone, format, custom cast, and deterministic seed.
2. **Deterministic story generation** with a story bible, continuity anchors, character arcs, balanced acts, chapters, scenes, and shot plans.
3. **Bounded TGRM repair** with explicit energy budgets, verification, rollback on regression, stop reasons, and scar memory.
4. **RYE and MSIL reporting** with before-and-after scores, repair yield, act balance, continuity, theme coherence, and collapse risk.
5. **Durable run workspaces** with atomic manifests, status, progress, warnings, timings, and artifact paths.
6. **Production bundles** containing screenplay text, film state, outline, TGRM audit, manifest, media, and result JSON.
7. **Command-line automation** for headless runs, validation, health checks, and run history.
8. **Controlled media rendering** that always keeps PNG cards and degrades safely when video encoding is unavailable.
9. **Deployment support** through Streamlit configuration, Docker, Compose, and a non-root container user.
10. **Automated verification** through compilation checks, 19 tests, smoke execution, and GitHub Actions.

## Important scope boundary

Silver-Screen currently produces a **screenplay and production blueprint**, plus optional short preview cards and reels. It does not yet render a finished 12, 45, or 90 minute cinematic film, and it does not currently call an external large language model or video-generation provider. The state contract is designed so those providers can be added later without replacing the operational core.

Voice files are inventoried only. This release does not clone, imitate, or synthesize a person's voice.

## Quick start

### Streamlit studio

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the URL Streamlit prints, normally `http://localhost:8501`.

### Command line

```bash
python -m silver_screen run \
  --brief examples/brief.json \
  --media cards \
  --output runs
```

The installed console command is equivalent:

```bash
silver-screen run --brief examples/brief.json --media cards
```

Useful operations:

```bash
python -m silver_screen validate --brief examples/brief.json
python -m silver_screen health --json
python -m silver_screen list --output runs
python -m silver_screen run \
  --premise "A courier discovers that every delivered letter changes yesterday." \
  --genre thriller \
  --format episode \
  --media off \
  --no-persist \
  --json
```

## Run workspace

Every persisted run receives a unique ID and a self-contained directory:

```text
runs/
  ss_20260730T040124Z_2d16a2d3/
    manifest.json          # status, stage, progress, warnings, metrics, artifact map
    brief.json             # normalized and replayable input
    film.json              # repaired structured film state
    outline.json           # compact cast, act, chapter, and scene plan
    screenplay.txt         # screenplay blueprint
    tgrm.json              # repair metrics, MSIL, cycles, scars, remaining fractures
    result.json            # complete pipeline response
    media/
      chapter_01.png
      chapter_01.mp4       # only when requested and available
      hero_reel.mp4        # only in hero mode
    <title>-<run-id>.zip   # complete portable production bundle
```

The storage path is fixed to `./runs`; deployments relocate it by changing the working directory or mounting that directory. Writes use a temporary file followed by an atomic replace. A core failure records a failed manifest. Optional media failures are isolated as warnings so the screenplay package remains usable.

## TGRM execution contract

```text
DETECT
  scene-count gaps, under-developed script density, missing ending,
  placeholder content, timeline breaks, act imbalance, character drift,
  late-story theme loss, repeated dialogue, and pacing collapse

MINIMAL CORRECTION
  choose the smallest repair that can address the highest-severity fracture
  and charge the configured energy budget

VERIFY
  compare before and after narrative scores and high-severity fracture counts

REINFORCE OR ROLLBACK
  keep verified improvements in scar memory; reject regressions without
  contaminating the accepted state
```

**RYE** is accepted `Delta R / energy spent`. **MSIL** combines continuity, act balance, theme coherence, character coverage, and repair oscillation into a stability report.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SILVER_SCREEN_RUNS_DIR` | `runs` | Allowlisted storage alias; only `runs` is accepted |
| `SILVER_SCREEN_DEBUG` | `0` | Show full Streamlit exceptions only when set to `1` |

The Streamlit upload limit is 20 MB per file. The media layer also enforces a 20 MB image-read limit.

## Docker

```bash
docker compose up --build
```

The service listens on port `8501` and mounts the fixed `./runs` directory into the container. The image includes FFmpeg and DejaVu fonts, runs as an unprivileged user, and exposes a Streamlit health check.

## Verification

```bash
python -m compileall -q silver_screen streamlit_app.py
python -m pytest -q
python -m silver_screen health --json
make smoke
```

The test suite covers validation, deterministic replay, every production format, balanced structure, TGRM repair, energy-budget gating, durable manifests, ZIP integrity, media cards, CLI execution, custom cast handling, health diagnostics, and workspace path safety.

## Architecture

```text
streamlit_app.py              interactive operational studio
silver_screen/
  science.py                  Reparodynamics constants and format contracts
  script_engine.py            deterministic story, scene, shot, and screenplay state
  tgrm.py                     detect, correct, verify, rollback, reinforce, RYE, MSIL
  media.py                    safe cards and optional preview reels
  runtime.py                  atomic manifests, durable runs, bundles, run history
  pipeline.py                 validated end-to-end orchestration and progress events
  health.py                   capability and storage diagnostics
  cli.py                      headless operations
  __main__.py                 python -m silver_screen entry point
tests/                        operational regression suite
.github/workflows/ci.yml      compile, test, health, and smoke verification
Dockerfile                    non-root production image
OPERATIONS.md                 deployment and recovery runbook
```

The existing TanStack code under `src/` remains a secondary interface. The Python pipeline is the authoritative production path in this release.

## Science references and provenance

- TGRM: [10.5281/zenodo.17273433](https://doi.org/10.5281/zenodo.17273433)
- Five-law system: [10.5281/zenodo.17538091](https://doi.org/10.5281/zenodo.17538091)
- Coding velocity simulation: [10.5281/zenodo.17336075](https://doi.org/10.5281/zenodo.17336075)
- Corpus: https://bonemantgrm.github.io/reparodynamics-corpus/

Reparodynamics is presented here as Cody Ryan Jenkins's conceptual engineering framework. The software metrics are operational heuristics for this system, not established clinical or physical measurements.

## License

No new license is implied by this operational release. Use the repository's existing licensing terms and attribution requirements.
