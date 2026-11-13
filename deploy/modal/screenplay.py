"""On-demand screenplay inference on Modal CPU."""

import json
import subprocess
import time
import urllib.request

import modal

app = modal.App('silver-screen-writer')

WRITER_SERVER = '/opt/screenplay/llama-b8400/llama-server'
WRITER_MODEL = '/opt/screenplay/qwen.gguf'
WRITER_URL = 'http://127.0.0.1:18891'
writer_image = (
    modal.Image.debian_slim(python_version='3.12')
    .apt_install('ca-certificates', 'curl', 'libgomp1')
    .run_commands(
        'mkdir -p /opt/screenplay',
        'curl -fLsS --retry 3 --connect-timeout 20 -o /tmp/llama.tar.gz '
        'https://github.com/ggml-org/llama.cpp/releases/download/b8400/llama-b8400-bin-ubuntu-x64.tar.gz',
        "echo 'e480a543bcbefb4ba8ad2e9fc732061a8b1ab4378e340b533d82f91f4209dd21  /tmp/llama.tar.gz' | sha256sum -c -",
        'tar -xzf /tmp/llama.tar.gz -C /opt/screenplay && rm /tmp/llama.tar.gz',
        f'curl -fLsS --retry 3 --connect-timeout 20 -o {WRITER_MODEL} '
        'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/'
        'a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
        f"echo '3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597  {WRITER_MODEL}' | sha256sum -c -",
    )
)


def _wait_for_model(process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 90
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(WRITER_URL + '/health', timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError('The screenplay model did not start.')


@app.function(image=writer_image, cpu=4, memory=(5120, 6144), max_containers=1, scaledown_window=5, timeout=600, retries=0)
def write_screenplay(payload: dict) -> dict:
    process = subprocess.Popen([
        WRITER_SERVER, '--model', WRITER_MODEL, '--alias', 'screenplay',
        '--host', '127.0.0.1', '--port', '18891', '--threads', '4', '--parallel', '1',
        '--ctx-size', '4096', '--gpu-layers', '0', '--no-repack', '--cache-ram', '0',
        '--reasoning', 'off', '--no-webui',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_for_model(process)
        request = urllib.request.Request(
            WRITER_URL + '/v1/chat/completions',
            data=json.dumps({**payload, 'stream': True}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        content = ''
        with urllib.request.urlopen(request, timeout=120) as response:
            for raw in response:
                if not raw.startswith(b'data: ') or raw.strip() == b'data: [DONE]':
                    continue
                choices = json.loads(raw[6:]).get('choices') or []
                if not choices:
                    continue
                content += choices[0].get('delta', {}).get('content') or ''
                if content.rstrip().endswith('}'):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        continue
        raise RuntimeError('The screenplay model returned an unfinished draft.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
