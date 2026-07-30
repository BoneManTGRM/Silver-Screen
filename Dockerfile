FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SILVER_SCREEN_RUNS_DIR=runs

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY silver_screen ./silver_screen
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip install --no-deps --no-build-isolation .

COPY streamlit_app.py ./
COPY .streamlit ./.streamlit

RUN useradd --create-home --uid 10001 silver \
    && mkdir -p /app/runs \
    && chown -R silver:silver /app
USER silver

EXPOSE 8501
VOLUME ["/app/runs"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).read()" || exit 1

CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
