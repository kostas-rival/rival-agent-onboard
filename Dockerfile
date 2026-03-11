# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

# Configure pip to use Artifact Registry as extra index
# Cloud Build service account has artifactregistry.reader role
RUN pip install --upgrade pip \
    && PIP_EXTRA_INDEX_URL=https://europe-west1-python.pkg.dev/rival-agents/rival-python-packages/simple/ \
       pip install .

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
