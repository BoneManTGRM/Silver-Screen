"""An editable shot list for Silver Screen's two-lead action starter."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Shot:
    title: str
    seconds: int
    reference: str
    prompt: str
    intimate: bool = False


SHOTS = (
    Shot("The evacuation", 8, "No photo", "Wide shot of a mountain evacuation station at dusk. Rain lashes an empty platform, distant explosions light the valley, and a rescue train waits under flickering lights. Slow cinematic push in."),
    Shot("The rescuer", 8, "Your photo", "Animate the adult lead from the opening frame. The rain-soaked rescue captain turns toward a flashing platform light, pulls up a jacket collar, and moves toward the train. Handheld medium shot."),
    Shot("The engineer", 8, "Co-star photo", "Animate the adult co-lead from the opening frame. The engineer looks up from a dim console as the lights go out, then grabs a flashlight. Close-up, cool window light and amber practical lighting."),
    Shot("The platform", 8, "Both of you in one photo", "Two adult leads sprint side by side across a rain-soaked platform toward a moving rescue train. A distant blast lights the sky and loose paper sweeps past. Wide tracking shot. Keep both faces and wardrobes consistent with the opening frame."),
    Shot("The last door", 8, "Both of you in one photo", "Inside a moving train, the rescue captain with a small streak of stage blood on one cheek steadies the engineer as the carriage jolts. They pull a sliding door shut and exchange a relieved look. Medium two-shot."),
    Shot("A quiet moment", 8, "Both of you in one photo", "In the quiet train compartment, the two fully clothed adult partners share a brief kiss and rest their foreheads together. Warm dawn light, steady close two-shot, gentle fade to black.", intimate=True),
    Shot("Out of the valley", 8, "No photo", "Wide exterior at sunrise. A rescue train emerges from a dark tunnel and crosses a misty valley. The light warms from blue to gold as the camera rises above the tracks."),
    Shot("Last look", 4, "Your photo", "Close shot of the adult rescue captain looking out the train window. In the reflection, warm morning light replaces the red glow of the night behind. Hold on the quiet expression, then fade out."),
)
