# DIFFER API: one persistent container (FastAPI + resident embedding model).
#
# Must not run serverless: every question is embedded with the same
# ~420 MB model that built the index, so the model has to stay loaded for
# the life of the process.
#
#   docker build -t differ-api .
#   docker run --env-file .env -e PORT=8100 -p 8100:8100 differ-api

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf \
    # Never call out to Hugging Face at runtime. The model is baked into the
    # image below; a fresh download on every restart is slow, rate-limited,
    # and a dependency on a third party being up.
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app

# CPU-only PyTorch first. The default wheel pulls ~2 GB of CUDA libraries a
# CPU host can never use. Same version as the tested local environment.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the embedding model in. It must be the same model the index was built
# with (EMBEDDING_MODEL, Requirement 13.3); override at build time if that
# ever changes.
ARG EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('${EMBEDDING_MODEL}')"

COPY services ./services
COPY jobs ./jobs
COPY db ./db

RUN useradd --create-home --uid 10001 differ \
    && chown -R differ:differ /app /opt/hf
USER differ

ENV PORT=8100
EXPOSE 8100

# --proxy-headers so the rate limiter sees the real client address behind
# the platform's load balancer instead of the balancer's own address.
CMD ["sh", "-c", "uvicorn services.api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
