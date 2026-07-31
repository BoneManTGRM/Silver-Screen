# Director Review and Production Resilience

Silver-Screen now separates three different transition operations:

1. **Cinematic Continuity** rebuilds an existing edit locally with video and audio crossfades. It does not call Replicate.
2. **Director Review** scores each boundary and identifies the incoming clips that still read as abrupt.
3. **Targeted transition retake** preserves the accepted incoming clip, reopens only that shot, generates one explicitly authorized replacement, and keeps the candidate with the stronger measured boundary score.

## Targeted retake workflow

1. Open **Director Review**.
2. Select a saved production with at least two verified clips.
3. Analyze the production.
4. Inspect the lowest-scoring boundaries.
5. Select one boundary.
6. Choose **Schedule retake only** to reopen the clip without a provider call, or explicitly authorize **Schedule and render one retake**.
7. Silver-Screen chains the previous verified final frame, adds a targeted match-on-action directive, and requests only the incoming clip.
8. The previous accepted candidate remains under `media/retakes/`.
9. After generation, both candidates are scored against the preceding clip.
10. Silver-Screen selects the stronger transition and records the decision in the durable queue.

The system does not silently spend credits on transition retakes. It processes one explicitly authorized retake at a time.

## Durable artifacts

A retake adds records similar to:

```text
runs/<run-id>/media/
  retakes/
    shot_0005/
      accepted_before_retake_01.mp4
      rejected_retake_01.mp4
  video_queue.json
  transition_plan.json
  transition_runtime.json
```

The queue records:

- selected transition
- original boundary score
- preserved candidate path
- new candidate score
- preserved candidate score
- selected candidate
- rejected candidate path
- authorization-adjusted provider-call ceiling
- retake history and timestamps

## Replicate 429 handling

Explicit HTTP 429 throttles use bounded automatic backoff. Silver-Screen reads `retry_after` or equivalent provider text, waits within the configured ceiling, and retries the same submission rather than immediately blocking the production.

Default controls:

```text
SILVER_SCREEN_PROVIDER_429_RETRIES=3
SILVER_SCREEN_PROVIDER_429_MAX_WAIT_SECONDS=60
```

This retry behavior is limited to explicit rate-limit responses. Ambiguous POST network failures are not automatically repeated because doing so could create a duplicate paid prediction.

## Director thresholds

```text
SILVER_SCREEN_TRANSITION_RETAKE_THRESHOLD=0.64
SILVER_SCREEN_TRANSITION_MAX_RETAKES=2
SILVER_SCREEN_TRANSITION_RETAKE_MIN_GAIN=0.015
```

Same-scene continuations use a stricter effective threshold because they are expected to look like the next moment of the same take. The minimum-gain setting prevents a newly generated candidate from replacing the preserved original unless it produces a measurable improvement.

## Cost behavior

- Transition analysis: local, no provider cost.
- Cinematic rebuild: local, no provider cost.
- Scheduling a retake: local, no provider cost.
- Rendering a retake: one additional Replicate prediction, only after explicit authorization.
- Candidate comparison and final assembly: local, no provider cost.

## Realistic scope

A local blend can mask a moderate visual discontinuity, but it cannot turn radically different generated footage into a literal continuous camera take. Director Review addresses that limit by regenerating only the weak incoming clip with the previous final frame and a targeted continuity directive, while preserving and comparing the original rather than discarding it.
