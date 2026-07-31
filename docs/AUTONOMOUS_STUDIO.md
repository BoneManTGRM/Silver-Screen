# Autonomous Studio

Silver-Screen 9 adds a one-click production path that coordinates the existing screenplay, Shot Director, prompt-ledger, video, visual-quality, transition, voice, and durable-run systems.

## What one click performs

1. Builds a provider-free screenplay and complete shot deck.
2. Runs the screenplay, prompt, and coverage audits.
3. Creates a hashed prompt ledger for every planned clip.
4. Loads or creates project-scoped production memory.
5. Builds a Production World Graph for characters, locations, props, scene chronology, and locked continuity facts.
6. Rebuilds the final prompt contract with memory included.
7. Creates model-independent recommendations for every shot while keeping execution on adapters known to be compatible.
8. Starts the durable Replicate production within explicit call and spend ceilings.
9. Uses existing TGRM retries and local visual-quality gates for technical failures.
10. Rebuilds the cinematic cut with adaptive transitions.
11. Optionally sends sampled generated frames to an authorized OpenAI vision model for semantic contract review.
12. Optionally generates voices and cinematic captions after the picture is complete.
13. Writes production memory, quality evidence, a project quality report, and a machine-readable edit-decision list into the run workspace.

## Persistent memory

Project memory is stored under:

```text
runs/_projects/<project-id>/production_memory.json
```

It retains bounded records of:

- character identity and wardrobe continuity
- locations, props, assets, scene chronology, and world facts
- accepted-shot evidence
- previous repair directives and scars
- model success and quality history
- creative and quality preferences

Memory collections are compacted automatically. Each run also receives an immutable snapshot under its own `memory/` directory.

## Semantic review

OpenAI semantic review is optional and requires both `OPENAI_API_KEY` and explicit operator authorization. It evaluates visible production evidence only. It does not identify people or infer private traits.

The supervisor checks:

- intended story beat
- visible action
- cast presence
- identity and wardrobe consistency
- props and world continuity
- framing and camera contract
- performance intent
- continuity with prior footage
- invented contradictions

When semantic review is unavailable, Silver-Screen labels the result as local visual QA only and does not pretend that semantic compliance was verified.

## Model routing

The router distinguishes the recommended model from the execution model. Specialist models can be recommended for performance, dialogue, action, animation, and lip synchronization, but they are not executed until a compatible adapter is enabled. The default Veo path remains the safe execution route.

## Blockbuster target

`Blockbuster target` increases creative strictness, prompt diversity, repair allowance, semantic quality targets, memory use, and continuity control. It is an orchestration target. Current generative models are probabilistic, so Silver-Screen does not guarantee that output will equal a human-produced Hollywood feature.

## Editing contract

Each autonomous run creates:

```text
edit/edit_decision_list.json
```

The document records source clips, timeline positions, transition handles, quality evidence, and lock state. It is the foundation for a future drag-and-drop non-linear editor without requiring accepted source footage to be regenerated.

## Operational limits

The current Streamlit deployment executes synchronously. A long one-click request can still be interrupted by hosting limits. Durable queues preserve accepted work, and the same autonomous job can be continued without restarting. A separate durable worker service remains the correct future architecture for unattended feature-length production.
