from __future__ import annotations

from contextlib import nullcontext
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import imageio_ffmpeg
import pytest
from modal import exception as modal_errors
from PIL import Image
from streamlit.testing.v1 import AppTest

from silver_screen.ai_video import generate_ai_video
from silver_screen.cast_library import PHOTO_SLOTS
from silver_screen.comfyui import ComfyUIClient, WAN_14B_VIDEO_SIZES, WORKFLOW_ENV
from silver_screen.modal_clip_archive import CLIPS_VOLUME
from test_long_video import _fake_assemble, _state


@pytest.fixture
def comfy_server(tmp_path, monkeypatch):
    graphs = {
        "image": {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__"}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        },
        "video": {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "__PROMPT__"}},
            "2": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"length": "__FRAMES__"}},
            "3": {"class_type": "CreateVideo", "inputs": {"fps": "__FPS__"}},
            "4": {"class_type": "SaveVideo", "inputs": {}},
        },
    }
    graphs["reference"] = {
        **graphs["video"],
        "5": {"class_type": "LoadImage", "inputs": {"image": "__IMAGE__"}},
    }
    for kind, graph in graphs.items():
        path = tmp_path / f"{kind}.json"
        path.write_text(json.dumps(graph), encoding="utf-8")
        monkeypatch.setenv(WORKFLOW_ENV[kind], str(path))

    png = io.BytesIO()
    Image.new("RGB", (10, 10)).save(png, format="PNG")
    clip = tmp_path / "sample.mp4"
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc2=s=192x108:d=8", "-c:v", "libx264", str(clip)],
        check=True,
    )
    media = {"result.png": png.getvalue(), "result.mp4": clip.read_bytes()}
    submissions = {}
    uploads = []

    class Handler(BaseHTTPRequestHandler):
        def send_data(self, data, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, payload):
            self.send_data(json.dumps(payload).encode(), "application/json")

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                uploads.append(data)
                self.send_json({"name": "reference.jpg", "subfolder": "silver"})
                return
            assert self.path == "/prompt"
            job_id = f"job-{len(submissions) + 1}"
            submissions[job_id] = json.loads(data)["prompt"]
            self.send_json({"prompt_id": job_id})

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/view":
                query = parse_qs(url.query)
                assert query["type"] == ["output"]
                assert query["subfolder"] == ["project media"]
                self.send_data(media[query["filename"][0]], "application/octet-stream")
                return
            job_id = url.path.removeprefix("/history/")
            workflow = submissions[job_id]
            is_image = any(node["class_type"] == "SaveImage" for node in workflow.values())
            self.send_json({job_id: {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {"1": {"images": [{
                    "type": "output", "subfolder": "project media",
                    "filename": "result.png" if is_image else "result.mp4",
                }]}},
            }})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        monkeypatch.setenv("SILVER_SCREEN_COMFYUI_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.delenv("SILVER_SCREEN_COMFYUI_TOKEN", raising=False)
        monkeypatch.setattr(ComfyUIClient, "poll_seconds", 0)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield submissions, uploads, media
        finally:
            server.shutdown()
            worker.join()


def test_direct_create_uses_comfyui_from_prompt_to_download(comfy_server):
    submissions, _, media = comfy_server
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    assert app.radio[1].value == "ComfyUI"
    prompt = "  Watercolor mountains.\nTitle: Night Train.  "
    app.text_area[0].set_value(prompt)
    app.button[0].click().run()
    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    assert submissions["job-1"]["1"]["inputs"]["text"] == prompt
    assert app.session_state["direct_create_job"]["data"] == media["result.png"]
    assert "Download image" in [item.proto.label for item in app.get("download_button")]

    app.radio[0].set_value("Video clip").run()
    app.text_area[0].set_value("Clay fox on a moonlit hill.")
    app.selectbox[0].select(4)
    app.button[0].click().run()
    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    assert submissions["job-2"]["2"]["inputs"]["length"] == 97
    assert submissions["job-2"]["3"]["inputs"]["fps"] == 24
    assert app.session_state["direct_create_job"]["data"] == media["result.mp4"]
    assert "Download original clip" in [item.proto.label for item in app.get("download_button")]


def test_comfyui_uploads_reference_and_binds_image_to_video_workflow(comfy_server):
    submissions, uploads, _ = comfy_server
    client = ComfyUIClient()
    prediction = client.create_prediction(
        "Pan left", seed=7, duration=6, image="data:image/jpeg;base64,ZmFrZS1qcGVn"
    )
    assert prediction["id"] == "job-1"
    assert b'Content-Disposition: form-data; name="image";' in uploads[0]
    assert b"fake-jpeg" in uploads[0]
    assert submissions["job-1"]["5"]["inputs"]["image"] == "silver/reference.jpg"
    assert submissions["job-1"]["2"]["inputs"]["length"] == 145


def test_direct_create_reenables_generate_after_modal_failure(monkeypatch):
    monkeypatch.setenv("SILVER_SCREEN_MODAL_APP", "test-renderer")
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    monkeypatch.setenv(WORKFLOW_ENV["video"], "video.json")
    prediction = {"id": "fc-test", "status": "starting"}
    results = iter([
        {"id": "fc-test", "status": "processing"},
        {
            "id": "fc-test",
            "status": "failed",
            "error": "ComfyUI did not start.",
        },
    ])
    client = SimpleNamespace(
        create_prediction=lambda *args, **kwargs: prediction,
        get_prediction=lambda call_id: next(results),
    )
    monkeypatch.setattr("silver_screen.comfyui.make_comfyui_client", lambda **kwargs: client)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    app.radio[0].set_value("Video clip").run()
    app.text_area[0].set_value("A paper pinwheel spins.")
    app.button[0].click().run()
    assert app.button[0].disabled
    app.button(key="direct-check-job").click().run()
    assert not app.exception
    assert app.error[0].value == "ComfyUI did not start."
    assert not app.button[0].disabled


def test_direct_create_displays_modal_clip_without_a_second_click(comfy_server, monkeypatch):
    _, _, media = comfy_server
    monkeypatch.setenv("SILVER_SCREEN_MODAL_APP", "test-renderer")
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    call_id = "fc-" + "1" * 26
    submissions = []
    checks = []

    def spawn(kind, prompt, settings):
        submissions.append((kind, prompt))
        return SimpleNamespace(object_id=call_id)

    def get(*, timeout):
        assert timeout == 0
        checks.append(timeout)
        if len(checks) == 1:
            raise modal_errors.TimeoutError
        return media["result.mp4"]

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        Function=SimpleNamespace(from_name=lambda *args: SimpleNamespace(spawn=spawn)),
        FunctionCall=SimpleNamespace(from_id=lambda call_id: SimpleNamespace(get=get)),
        exception=modal_errors,
    ))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    app.radio[0].set_value("Video clip").run()
    app.text_area[0].set_value("A paper pinwheel spins.")
    app.button[0].click().run()
    assert app.info[0].value.startswith("Waiting for Modal.")
    assert app.button[0].disabled
    assert app.query_params == {"render": call_id, "render_seconds": "8"}

    # AppTest has no browser timer, so simulate the scheduled refresh without clicking a widget.
    app.run()
    assert not app.exception
    assert not app.error
    assert not app.button[0].disabled
    assert app.session_state["direct_create_job"]["data"] == media["result.mp4"]
    assert app.get("video")
    assert "Download original clip" in [item.proto.label for item in app.get("download_button")]
    assert submissions == [("video", "A paper pinwheel spins.")]
    assert len(checks) == 2


def test_direct_create_reloads_modal_job_and_reports_a_manual_check(comfy_server, monkeypatch):
    _, _, media = comfy_server
    monkeypatch.setenv("SILVER_SCREEN_MODAL_APP", "test-renderer")
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    call_id = "fc-" + "2" * 26
    lookups = []

    def from_id(render_id):
        lookups.append(render_id)

        def get(*, timeout):
            assert timeout == 0
            if len(lookups) < 3:
                raise modal_errors.TimeoutError
            return media["result.mp4"]

        return SimpleNamespace(get=get)

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(from_id=from_id), exception=modal_errors,
    ))
    page = Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py"
    app = AppTest.from_file(page)
    app.query_params.update({"render": call_id, "render_seconds": "8"})
    app.run()
    assert app.radio[0].value == "Video clip"
    assert app.button[0].disabled
    app.button(key="direct-check-job").click().run()
    assert any(caption.value.startswith("Last manual check") for caption in app.caption)
    assert app.info[0].value.startswith("Waiting for Modal.")

    # A new connection has no session state and still retrieves the existing job.
    reopened = AppTest.from_file(page)
    reopened.query_params.update(app.query_params)
    reopened.run()
    assert not reopened.exception
    assert not reopened.error
    assert not reopened.button[0].disabled
    assert reopened.get("video")
    assert "Download original clip" in [item.proto.label for item in reopened.get("download_button")]
    assert reopened.session_state["direct_create_job"]["data"] == media["result.mp4"]
    assert "8-second clip" in [caption.value for caption in reopened.caption]
    assert lookups == [call_id] * 3


def test_recover_modal_clip_with_image_selected(comfy_server, monkeypatch):
    _, _, media = comfy_server
    monkeypatch.setenv("SILVER_SCREEN_MODAL_APP", "test-renderer")
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    call_id = "fc-" + "1" * 26
    checked = []

    def from_id(render_id):
        checked.append(render_id)
        return SimpleNamespace(get=lambda **kwargs: media["result.mp4"])

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        FunctionCall=SimpleNamespace(from_id=from_id), exception=modal_errors,
    ))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    assert app.radio[0].value == "Image"
    next(field for field in app.text_input if field.label == "ComfyUI prediction ID").set_value(call_id)
    next(button for button in app.button if button.label == "Find render").click().run()
    assert not app.exception
    assert not app.error
    assert app.get("video")
    assert "Download original clip" in [item.proto.label for item in app.get("download_button")]
    assert app.session_state["direct_create_job"]["data"] == media["result.mp4"]
    assert app.query_params == {"render": call_id}
    assert checked == [call_id]


def test_bundled_wan_accepts_custom_format_and_director_controls(comfy_server, monkeypatch, tmp_path):
    submissions, uploads, _ = comfy_server
    workflows = Path(__file__).resolve().parents[1] / "deploy/runpod/workflows"
    monkeypatch.setenv(WORKFLOW_ENV["video"], str(workflows / "wan-video.json"))
    monkeypatch.setenv(WORKFLOW_ENV["reference"], str(workflows / "wan-reference.json"))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    app.radio[0].set_value("Video clip").run()
    app.text_area[0].set_value("  A train at dusk.  ")
    app.text_area[1].set_value("Camera shake")
    app.number_input[0].set_value(3)
    app.selectbox[0].select("Portrait HD · 704 × 1280")
    app.slider[0].set_value(27)
    app.text_input[0].set_value("542")
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    graph = submissions["job-1"]
    latent = next(node["inputs"] for node in graph.values() if node["class_type"] == "Wan22ImageToVideoLatent")
    assert (latent["width"], latent["height"], latent["length"]) == (704, 1280, 73)
    sampler = next(node["inputs"] for node in graph.values() if node["class_type"] == "KSampler")
    assert (sampler["steps"], sampler["seed"]) == (27, 542)
    assert {node["inputs"].get("text") for node in graph.values()} >= {"  A train at dusk.  ", "Camera shake"}

    ComfyUIClient().create_prediction("Another train", seed=1, duration=2, video_size=(1280, 704),
                                     image="data:image/jpeg;base64,ZmFrZS1qcGVn")
    assert uploads
    assert any(node["inputs"].get("width") == 1280 for node in submissions["job-2"].values())

    if not shutil.which("ffprobe"):
        return
    next(button for button in app.button if button.label == "Add clip to custom film").click().run()
    assert not app.exception
    next(widget for widget in app.selectbox if widget.label == "Custom film frame size").select("Portrait HD · 720 × 1280")
    next(button for button in app.button if button.label == "Export custom film").click().run(timeout=20)
    assert not app.exception
    assert not app.error
    assert "Download custom film" in [item.proto.label for item in app.get("download_button")]
    exported = tmp_path / "portrait.mp4"
    exported.write_bytes(app.session_state["custom_film_export"])
    dimensions = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
        "-of", "csv=p=0", str(exported),
    ], text=True).strip()
    assert dimensions == "720,1280"


def test_direct_create_previews_full_photo_and_guides_hand_motion(comfy_server, monkeypatch, tmp_path):
    submissions, uploads, _ = comfy_server
    workflows = Path(__file__).resolve().parents[1] / "deploy/runpod/workflows"
    monkeypatch.setenv(WORKFLOW_ENV["video"], str(workflows / "wan-video.json"))
    monkeypatch.setenv(WORKFLOW_ENV["reference"], str(workflows / "wan-reference.json"))
    monkeypatch.setenv("SILVER_SCREEN_CAST_LIBRARY_DIR", str(tmp_path))
    photo = Image.new("RGB", (600, 1000), "green")
    photo.paste("red", (0, 0, 600, 100))
    photo.paste("blue", (0, 900, 600, 1000))
    photo.save(tmp_path / f"{PHOTO_SLOTS['Your photo']}.jpg")

    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    app.radio[0].set_value("Video clip").run()
    next(box for box in app.selectbox if box.label == "Or use a saved cast photo").select("Your photo").run()
    assert any(image.proto.imgs[0].caption == "Starting frame · 704 × 1280" for image in app.image)
    next(box for box in app.checkbox if box.label == "Keep hands in frame").check()
    app.text_area[0].set_value("A runner warms up.")
    app.text_area[1].set_value("camera shake")
    app.button[0].click().run()
    assert not app.exception
    assert not app.error

    graph = submissions["job-1"]
    assert graph["1"]["inputs"]["unet_name"] == "wan2.2_ti2v_5B_fp16.safetensors"
    assert graph["6"]["inputs"]["width"] == 704
    assert graph["6"]["inputs"]["height"] == 1280
    assert "Keep both hands visible" in graph["4"]["inputs"]["text"]
    assert "camera shake, hands out of frame, missing hands" in graph["5"]["inputs"]["text"]
    jpeg = uploads[0].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
    with Image.open(io.BytesIO(jpeg)) as fitted:
        assert fitted.size == (704, 1280)
        assert fitted.getpixel((352, 70))[0] > 230
        assert fitted.getpixel((352, 1210))[2] > 230


def test_direct_create_can_submit_the_14b_photo_workflow(comfy_server, monkeypatch, tmp_path):
    _, _, media = comfy_server
    workflows = Path(__file__).resolve().parents[1] / "deploy/runpod/workflows"
    monkeypatch.setenv(WORKFLOW_ENV["video"], str(workflows / "wan-video.json"))
    monkeypatch.setenv(WORKFLOW_ENV["reference"], str(workflows / "wan-reference.json"))
    monkeypatch.setenv("SILVER_SCREEN_CAST_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setenv("SILVER_SCREEN_MODAL_APP", "test-renderer")
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    Image.new("RGB", (720, 1280), "blue").save(tmp_path / f"{PHOTO_SLOTS['Your photo']}.jpg")
    call_id = "fc-" + "5" * 26
    submissions = []

    def lookup(app_name, function):
        def spawn(kind, prompt, settings):
            submissions.append((app_name, function, kind, prompt, settings))
            return SimpleNamespace(object_id=call_id)
        return SimpleNamespace(spawn=spawn)

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        Function=SimpleNamespace(from_name=lookup),
        FunctionCall=SimpleNamespace(from_id=lambda _: SimpleNamespace(get=lambda **_: media["result.mp4"])),
        exception=modal_errors,
    ))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    app.radio[0].set_value("Video clip").run()
    assert not any(box.label == "Render model" for box in app.selectbox)
    next(box for box in app.selectbox if box.label == "Or use a saved cast photo").select("Your photo").run()
    models = next(box for box in app.selectbox if box.label == "Render model")
    assert models.value == "Wan 2.2 5B · 24 fps"
    models.select("Wan 2.2 14B · 16 fps").run()
    assert app.number_input[0].max == 5
    assert any(image.proto.imgs[0].caption == "Starting frame · 720 × 1280" for image in app.image)
    assert app.slider[0].value == 20
    next(box for box in app.checkbox if box.label == "Keep hands in frame").check()
    app.text_area[0].set_value("The runner lifts both hands.")
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert app.get("video")
    app_name, function, kind, prompt, settings = submissions[0]
    assert len(submissions) == 1
    assert (app_name, function, kind) == ("test-renderer", "render_wan14", "video")
    assert "Keep both hands visible" in prompt
    assert "blurry subject" in settings["negative_prompt"]
    assert "missing hands" in settings["negative_prompt"]
    assert settings["video_size"] == (720, 1280)
    assert settings["save_clip"] is True
    assert settings["duration"] == 5
    assert settings["image"].startswith("data:image/jpeg;base64,")
    assert "wan_14b" not in settings


def test_14b_workflow_preserves_the_two_denoising_stages(comfy_server, monkeypatch):
    submissions, _, _ = comfy_server
    workflows = Path(__file__).resolve().parents[1] / "deploy/runpod/workflows"
    monkeypatch.setenv(WORKFLOW_ENV["reference"], str(workflows / "wan-reference-14b.json"))
    monkeypatch.setenv("SILVER_SCREEN_COMFYUI_FPS", "16")
    client = ComfyUIClient()
    client.video_sizes = WAN_14B_VIDEO_SIZES
    client.create_prediction("Slow movement", seed=42, duration=5, video_size=(1280, 720), steps=25,
                             image="data:image/jpeg;base64,ZmFrZS1qcGVn")
    graph = submissions["job-1"]
    conditioning = graph["8"]["inputs"]
    assert (conditioning["width"], conditioning["height"], conditioning["length"]) == (1280, 720, 81)
    high_noise = graph["11"]["inputs"]
    low_noise = graph["12"]["inputs"]
    assert high_noise["noise_seed"] == 42
    assert high_noise["end_at_step"] == low_noise["start_at_step"] == 12
    assert high_noise["steps"] == low_noise["steps"] == low_noise["end_at_step"] == 25
    assert high_noise["return_with_leftover_noise"] == "enable"
    assert low_noise["add_noise"] == "disable"
    assert low_noise["latent_image"] == ["11", 0]
    assert graph["14"]["inputs"]["fps"] == 16


@pytest.mark.parametrize("archive_fails", [False, True])
def test_modal_worker_archives_completed_clip_without_a_viewer(comfy_server, monkeypatch, tmp_path, archive_fails):
    from deploy.modal import worker

    _, _, media = comfy_server
    render_id = "fc-" + "3" * 26
    clip = media["result.mp4"]
    archive = tmp_path / f"{render_id}.mp4"
    commits = []

    def commit():
        assert archive.read_bytes() == clip
        commits.append(archive)
        if archive_fails:
            raise RuntimeError("test archive failure")

    def submit(prompt, **settings):
        assert settings == {"duration": 8}
        return {"id": "comfy-job", "status": "succeeded"}

    client = SimpleNamespace(create_prediction=submit, wait=lambda prediction: prediction,
                             read_output=lambda prediction: clip)
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda **kwargs: None)
    monkeypatch.setattr(worker, "CLIPS_MOUNT", tmp_path)
    monkeypatch.setattr(worker, "clips_volume", SimpleNamespace(commit=commit))
    monkeypatch.setattr(worker.modal, "current_function_call_id", lambda: render_id)
    monkeypatch.setattr(worker, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: process))
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr("silver_screen.comfyui.ComfyUIClient", lambda **kwargs: client)
    # The remote worker has no browser or Streamlit session.
    with pytest.warns(UserWarning, match="executing locally"):
        result = worker.render.local("video", "The runner moves.", {"duration": 8, "save_clip": True})
    assert result == {"data": clip, "archive_failed" if archive_fails else "archived": True}
    assert commits == [archive]
    assert list(tmp_path.glob(".render-*")) == []


def test_direct_create_opens_saved_clip_without_a_call_result(comfy_server, monkeypatch):
    _, _, media = comfy_server
    for key, value in {"MODAL_TOKEN_ID": "test-id", "MODAL_TOKEN_SECRET": "test-secret",
                       "SILVER_SCREEN_MODAL_APP": "test-renderer"}.items():
        monkeypatch.setenv(key, value)
    render_id = "fc-" + "4" * 26

    def read_file(path):
        assert path == f"{render_id}.mp4"
        yield media["result.mp4"]

    def from_name(name):
        assert name == CLIPS_VOLUME
        return SimpleNamespace(
            iterdir=lambda *args, **kwargs: [SimpleNamespace(path=f"/{render_id}.mp4", size=10, mtime=7)],
            read_file=read_file,
        )

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        Volume=SimpleNamespace(from_name=from_name), exception=modal_errors,
    ))
    reopened = AppTest.from_file(Path(__file__).resolve().parents[1] / "pages/0_Direct_Create.py").run()
    next(button for button in reopened.button if button.label == "Load saved clips").click().run()
    next(box for box in reopened.selectbox if box.label == "Recent clips").select(render_id).run()
    next(button for button in reopened.button if button.label == "Open saved clip").click().run()
    assert not reopened.exception
    assert not reopened.error
    assert reopened.get("video")
    assert reopened.session_state["direct_create_job"]["data"] == media["result.mp4"]
    assert "Saved in your studio" in [caption.value for caption in reopened.caption]


def test_movie_uses_comfyui_and_keeps_backend_on_resume(comfy_server, tmp_path, monkeypatch):
    submissions, _, _ = comfy_server
    monkeypatch.setenv("SILVER_SCREEN_VIDEO_BACKEND", "comfyui")
    monkeypatch.delenv(WORKFLOW_ENV["reference"])
    monkeypatch.setenv("SILVER_SCREEN_VISUAL_QUALITY_GATE", "0")
    monkeypatch.setattr("silver_screen.ai_video.assemble_clips", _fake_assemble)
    out = tmp_path / "production"
    settings = {"target_runtime_seconds": 16, "max_shots": 2, "batch_size": 1}
    first = generate_ai_video(_state(), out, **settings)
    assert first["provider"] == "comfyui"
    assert first["queue"]["backend"] == "comfyui"
    assert first["status"] == "partial"
    assert first["metrics"]["verifiedShots"] == 1
    assert len(submissions) == 1

    monkeypatch.setenv("SILVER_SCREEN_VIDEO_BACKEND", "replicate")
    second = generate_ai_video(_state(), out, resume=True, **settings)
    assert second["provider"] == "comfyui"
    assert second["status"] == "complete"
    assert second["metrics"]["verifiedShots"] == 2
    assert len(submissions) == 2
