# Changelog

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
- Added a 19-test regression suite and GitHub Actions verification.
- Clarified the current boundary between production blueprints and finished generative films.
