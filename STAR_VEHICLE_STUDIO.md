# Star Vehicle Studio

Silver-Screen is a general-purpose film production system. Moonie Moo is an optional example starter, not the product identity or required subject.

## Put yourself in a movie

1. Open **Star Vehicle Studio**.
2. Select **Star as myself**.
3. Enter your name, role, appearance, wardrobe, and identity details that must not drift.
4. Upload one to six recent authorized reference images.
5. Select the strongest clean medium or full-body image as the primary identity anchor.
6. Confirm that you are the person shown or have explicit permission to use the likeness.
7. Start with **One-clip identity test**.
8. Review the eight-second clip before extending or continuing the same saved production.
9. When the visual identity is acceptable, select a full blueprint or use **Extend Existing Production** so the verified test clip is not discarded.
10. Use **Professional Script Sync** or **Voice Studio** for dialogue, narration, subtitles, and the final voiced cut.

## Identity behavior

The current Replicate/Veo path accepts one primary starting image for the first clip. Silver-Screen then chains verified final frames to preserve visual continuity. Additional uploaded references are retained inside the run's `identity/` directory for audit, continuity review, and future provider capabilities.

The lead's cast description contains an explicit identity lock on every scene prompt. This improves consistency but does not guarantee perfect identity preservation because the external generative-video model remains probabilistic.

Silver-Screen does not perform facial recognition and does not create biometric embeddings. It stores the authorized images as ordinary production assets in the selected run workspace.

## Project starters

- Blank original project
- Star as myself
- Authorized real person
- Original fictional character
- Moonie Moo example

All starters remain editable. No starter changes the underlying pipeline or locks the repository to one character.

## Provider requirements

Generated video requires:

```text
REPLICATE_API_TOKEN
```

Generated speech optionally uses one of:

```text
OPENAI_API_KEY
ELEVENLABS_API_KEY
```

Finished authorized audio tracks can be uploaded without a speech-provider API.
