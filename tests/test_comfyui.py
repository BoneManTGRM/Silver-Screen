from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import imageio_ffmpeg
import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from silver_screen.ai_video import generate_ai_video
from silver_screen.comfyui import ComfyUIClient, WORKFLOW_ENV
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
    assert app.get("download_button")[0].proto.label == "Download image"

    app.radio[0].set_value("Video clip").run()
    app.text_area[0].set_value("Clay fox on a moonlit hill.")
    app.selectbox[0].select(4)
    app.button[0].click().run()
    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    assert submissions["job-2"]["2"]["inputs"]["length"] == 97
    assert submissions["job-2"]["3"]["inputs"]["fps"] == 24
    assert app.session_state["direct_create_job"]["data"] == media["result.mp4"]
    assert app.get("download_button")[0].proto.label == "Download clip"


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
