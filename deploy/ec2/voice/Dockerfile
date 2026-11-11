FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 torchaudio==2.6.0 \
    && pip install --no-cache-dir chatterbox-tts==0.1.7 spacy-pkuseg==1.0.2

# Chatterbox initializes this tokenizer even for English and Spanish.
ENV PKUSEG_HOME=/opt/pkuseg
RUN mkdir -p "$PKUSEG_HOME" \
    && cd "$PKUSEG_HOME" \
    && curl --fail --location --retry 3 --connect-timeout 25 --max-time 150 \
        --output spacy_ontonotes.zip \
        https://github.com/explosion/spacy-pkuseg/releases/download/v0.0.26/spacy_ontonotes.zip \
    && echo 'b216e7f92de7ae285aeab8feba2faa8ea8216e5995ff6fb3d391cc8356db1bfe  spacy_ontonotes.zip' | sha256sum -c - \
    && python -m zipfile -e spacy_ontonotes.zip spacy_ontonotes

WORKDIR /app
COPY worker.py .
ENV HF_HOME=/cache HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 OMP_NUM_THREADS=2
CMD ["python", "worker.py"]
