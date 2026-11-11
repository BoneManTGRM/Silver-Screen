# Silver Screen on one RunPod pod

The pod runs the browser studio and a local ComfyUI server. It downloads
FLUX.1 Schnell FP8 and Wan 2.2 5B on the first boot. There is no Railway service
or Replicate token to configure.

## Create the pod

In [RunPod Pods](https://console.runpod.io/pods), use an on-demand RTX 4090 or
RTX 3090 and enter these custom container settings. The initial GPU rental
also covers package installation and model downloads.

| Setting | Value |
| --- | --- |
| Container image | `runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404` |
| HTTP ports | `8080` |
| TCP ports | Leave empty |
| Container disk | 30 GB |
| Network volume | 100 GB, attached at `/workspace` in the pod's region |
| Environment variable | `SILVER_SCREEN_PASSWORD` set to a private password of at least 11 characters |

Set the container start command to the following.

```bash
bash -lc 'set -e; if [ ! -d /workspace/Silver-Screen/.git ]; then git clone --depth 1 --branch feature/direct-create https://github.com/BoneManTGRM/Silver-Screen.git /workspace/Silver-Screen; fi; exec bash /workspace/Silver-Screen/deploy/runpod/start.sh'
```

Once the logs show Caddy and Streamlit have started, open **Connect → HTTP
Service [Port 8080]**. Sign in with username `silver-screen` and the password
you set. Use **Direct Create** to generate an image and a four-second video
before starting a longer production. ComfyUI listens only inside the pod.

## Stopping and restarting

Save or download any finished renders you want before stopping the pod. Wait
for active GPU jobs to finish before stopping it. ComfyUI's live execution
history is lost when its process restarts. Models and Silver Screen's saved
productions stay on the persistent volume when the same pod restarts.
The network volume is billed separately while the pod is stopped. It can
be attached to another pod in the same data center.
