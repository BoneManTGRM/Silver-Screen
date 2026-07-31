# Cinematic Continuity Engine

Silver-Screen generates short provider clips and assembles them into a longer
film. A plain concat creates an obvious cut even when the next clip starts from
the previous final frame. The Cinematic Continuity Engine treats every boundary
as a production artifact instead of an unexamined join.

## What it does

For each pair of verified clips, Silver-Screen now:

1. Classifies the boundary as a same-scene continuation, scene change, or
   chapter change.
2. Adds a provider prompt directive for camera velocity, actor pose, screen
   direction, wardrobe, lighting, and match-on-action continuity.
3. Extracts the preceding final frame and following first frame when FFmpeg is
   available.
4. Measures visual, luminance, and edge similarity.
5. Applies the smallest local TGRM repair:
   - short cross dissolve for an already aligned continuation;
   - gentle cross dissolve for a normal scene change;
   - longer cross dissolve when a continuation is visibly discontinuous;
   - controlled dip-to-black when a new scene cannot be hidden cleanly.
6. Normalizes frame rate, resolution, time base, and audio layout.
7. Uses FFmpeg `xfade` for video and `acrossfade` for audio.
8. Produces a separate `final_cinematic_film.mp4` or
   `partial_cinematic_film.mp4`.
9. Saves the transition plan, scores, timeline, fallback history, and artifacts.

This work is local. It does not create another Replicate prediction.

## Existing films

Open **Cinematic Continuity** in the Streamlit page navigation. Select a saved
production and press **Rebuild smooth cinematic cut**.

The page can rebuild any run with at least two verified clips. It preserves:

- every verified source clip;
- previous hard-cut assemblies;
- video queue and prediction history;
- voice and script artifacts.

## Artifacts

Each run's `media/` directory can contain:

```text
transition_plan.json
transition_runtime.json
transition_frames/
chapters/chapter_001_cinematic.mp4
partial_cinematic_film.mp4
final_cinematic_film.mp4
```

The transition plan records:

- source and destination shot;
- story relationship;
- selected edit style;
- blend duration;
- raw and effective scores;
- TGRM edit repair;
- overlap-adjusted timeline;
- assembly fallback state.

## Safe fallback

If the installed FFmpeg build cannot complete the preferred transition graph,
Silver-Screen retries with the broadly supported `fade` transition. If that also
fails, it preserves the production and uses the previous verified concat path
rather than discarding footage or requesting another paid clip.

## Configuration

```text
SILVER_SCREEN_CINEMATIC_TRANSITIONS=1
SILVER_SCREEN_TRANSITION_MODE=auto
SILVER_SCREEN_TRANSITION_SAME_SCENE_SECONDS=0.18
SILVER_SCREEN_TRANSITION_SCENE_SECONDS=0.32
SILVER_SCREEN_TRANSITION_CHAPTER_SECONDS=0.50
SILVER_SCREEN_TRANSITION_ANALYZE_FRAMES=1
SILVER_SCREEN_TRANSITION_FPS=24
SILVER_SCREEN_TRANSITION_MAX_WIDTH=1280
SILVER_SCREEN_TRANSITION_CRF=18
```

`SILVER_SCREEN_TRANSITION_MODE` accepts:

- `auto`: balanced default;
- `subtle`: shorter blends;
- `strong`: longer masking for difficult joins;
- `off`: retain safe concat behavior.

## Operational limit

Local editing can hide or soften many discontinuities, but it cannot turn two
materially different independently generated performances into a literal single
camera take. The transition report marks difficult boundaries as `attention`.
Those boundaries can later receive a targeted provider retake while all other
verified footage remains accepted.
