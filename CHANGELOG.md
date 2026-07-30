# Changelog

## 3.0.0

- Replaced the fixed eight-clip AI-video path with a target-runtime shot planner.
- Added durable `video_queue.json`, `video_runtime.json`, and `video_scar_memory.json` checkpoints.
- Added resumable provider predictions so interrupted runs reuse persisted prediction IDs and accepted clips.
- Added Reparodynamics production phases across plan, generate, detect, repair, verify, stabilize, reinforce, and continue.
- Added TGRM shot-level repair with bounded retries, prompt repair, seed repair, rollback, and scar reinforcement.
- Added final-frame extraction and continuity chaining between verified clips.
- Added whole-production provider-call and estimated-spend gates.
- Added video RYE, video MSIL, failure rate, continuity coverage, repair oscillation, and stop reasons.
- Added chapter-first assembly, verified partial films, and final-film assembly.
- Added `resume-video`, `video-status`, and `list-resumable` CLI operations.
- Added Streamlit controls for target runtime, batch checkpoints, repairs, budgets, continuity, and resume.
- Added regression coverage for planning, queue extension, checkpoint/resume, targeted repairs, scar memory, and budget gates.

## 2.0.0

- Restored the missing `silver_screen.script_engine` runtime dependency.
- Replaced random story identifiers with deterministic seeded identifiers.
- Added normalized brief validation, custom cast support, and replay fingerprints.
- Added story bible, continuity anchors, balanced acts, chapters, scenes, and shot plans.
- Added TGRM energy budgets, verification, rollback, stop reasons, and deduplicated scar memory.
- Added durable run manifests, atomic writes, run history, artifact maps, and ZIP bundles.
- Added CLI run, validate, health, and list operations.
- Added safe media degradation, PNG cards, optional chapter videos, and hero reels.
- Added runtime health diagnostics, Streamlit production controls, Docker, Compose, and configuration.
- Constrained persisted output to an allowlisted `./runs` mount so user input cannot control filesystem paths.
- Added a regression suite and GitHub Actions verification.
