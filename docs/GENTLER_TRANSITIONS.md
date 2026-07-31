# Gentler transition defaults

Silver-Screen now uses slightly longer local overlap in automatic cinematic mode:

- same-scene continuation: 0.26 seconds
- scene change: 0.40 seconds
- chapter change: 0.58 seconds

Measured weak same-scene boundaries are extended adaptively up to 0.42 seconds. Explicit environment values continue to override these defaults. Rebuilding a saved production remains a local FFmpeg operation and does not create Replicate calls.
