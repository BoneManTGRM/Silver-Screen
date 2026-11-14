from unittest.mock import Mock

import pytest

from silver_screen.ai_video import VideoGenerationError
from silver_screen.direct_generation import CLIP_MODEL, IMAGE_MODEL, direct_video_prompt, submit_direct_generation


def test_natural_pace_directs_motion_and_preserves_the_action():
    prompt = "Two dancers take a step apart, then raise their arms."
    directed = direct_video_prompt(prompt, natural_pace=True)

    assert directed.startswith(prompt + "\n\n")
    assert "once, in order, at natural real-time speed" in directed
    assert "foot plant and the weight transfer" in directed


def test_direct_video_prompt_preserves_explicit_timing_and_empty_input():
    prompt = "  A dancer raises both arms in slow motion.  "
    assert direct_video_prompt(prompt, natural_pace=False) == prompt
    assert direct_video_prompt(" \n ", natural_pace=True) == " \n "


@pytest.mark.parametrize(
    ("model", "settings"),
    [
        (IMAGE_MODEL, {"output_format": "png"}),
        (CLIP_MODEL, {"duration": 6, "resolution": "720p", "generate_audio": False}),
    ],
)
def test_direct_prediction_preserves_prompt_and_sends_model_specific_inputs(model, settings):
    client = Mock(model=model)
    prompt = "  Clay animation.\nTitle on screen: Night Train.  "

    submit_direct_generation(client, prompt, aspect_ratio="9:16", duration=6, generate_audio=False)

    client._request_json.assert_called_once_with(
        "POST",
        f"https://api.replicate.com/v1/models/{model}/predictions",
        {"input": {"prompt": prompt, "aspect_ratio": "9:16", **settings}},
    )


def test_empty_prompt_does_not_submit_a_paid_prediction():
    client = Mock(model=IMAGE_MODEL)
    with pytest.raises(VideoGenerationError, match="Enter a prompt"):
        submit_direct_generation(client, " \n ", aspect_ratio="1:1")
    client._request_json.assert_not_called()
