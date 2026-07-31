# Closed-loop Autonomous Director

When OpenAI semantic review is configured and explicitly authorized, Autonomous Studio can use remaining calls inside the operator-approved provider ceiling to repair the weakest completed shot.

The loop is bounded:

1. Inspect verified footage against the approved shot contract and persistent production memory.
2. Select the lowest-scoring semantic unit.
3. Confirm that an approved provider call and per-shot attempt remain.
4. Preserve the currently accepted MP4 and its technical and semantic reports.
5. Add one targeted repair directive after the locked prompt contract.
6. Generate only that shot.
7. Inspect the replacement.
8. Keep the replacement only when it removes a hard failure or produces the configured minimum score gain.
9. Restore the preserved original automatically when the replacement is worse, ambiguous, or the retake fails.
10. Rebuild the cinematic cut and generate speech only after picture lock.

The loop never raises the provider-call or dollar ceiling. It stops when all reviewed shots reach the target, the repair limit is reached, no approved call remains, or the next retake would exceed the per-shot attempt ceiling.

Configuration:

```text
SILVER_SCREEN_AUTONOMOUS_AUTO_SEMANTIC_REPAIR=1
SILVER_SCREEN_AUTONOMOUS_MAX_SEMANTIC_RETAKES_PER_RUN=2
SILVER_SCREEN_AUTONOMOUS_MAX_SEMANTIC_RETAKES_PER_SHOT=2
SILVER_SCREEN_AUTONOMOUS_RETAKE_MIN_GAIN=0.015
```

This verifies improvement against the system's shot-contract evidence. It does not guarantee that current generative-video models will produce a Hollywood-studio result.
