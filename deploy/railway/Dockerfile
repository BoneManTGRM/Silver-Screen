FROM caddy:2.10 AS proxy
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=proxy /usr/bin/caddy /usr/bin/caddy
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt modal==1.6.4
COPY silver_screen ./silver_screen
COPY pages ./pages
COPY streamlit_app.py ./
COPY .streamlit ./.streamlit
COPY deploy/iphone/silver-screen-offline.js ./deploy/iphone/
COPY deploy/runpod/workflows ./deploy/runpod/workflows
COPY deploy/railway/Caddyfile /etc/caddy/Caddyfile
COPY deploy/railway/start.sh /app/start.sh
ENV SILVER_SCREEN_VIDEO_BACKEND=comfyui \
    SILVER_SCREEN_CAST_LIBRARY_DIR=/app/runs/cast \
    SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW=/app/deploy/runpod/workflows/flux-schnell.json \
    SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW=/app/deploy/runpod/workflows/wan-video.json \
    SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW=/app/deploy/runpod/workflows/wan-reference.json \
    SILVER_SCREEN_COMFYUI_FPS=24
RUN useradd --create-home --uid 10001 silver && mkdir -p /app/runs && chown silver:silver /app/runs
USER silver
CMD ["bash", "/app/start.sh"]
