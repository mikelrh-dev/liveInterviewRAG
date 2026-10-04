# syntax=docker/dockerfile:1

# Runtime image for InterviewTTS. Mirrors deployment/interviewtts.service: same
# entry point, same non-root posture, same writable path set -- expressed as a
# container instead of a systemd unit.
#
# Two files are read by the build and NOTHING else. The COPY list below is
# explicit rather than `COPY . .` on purpose: .dockerignore already excludes the
# secrets and the PII, and a broad COPY would make that file the only thing
# standing between `OPENROUTER_API_KEY` and a published layer. Two hard-coded
# paths fail loudly if someone adds a directory the app needs; `COPY . .`
# fails silently if someone forgets one.
FROM python:3.10-slim

# ffmpeg is a RUNTIME dependency, not a build one: faster-whisper hands the
# uploaded recording to it for decoding. The host proves it (the app transcribes
# today on a machine whose ffmpeg lives under WinGet\Links), and a container
# cannot reach a Windows path.
#
# ca-certificates is what makes TLS work for edge-tts and for
# httpx -> OpenRouter. python:slim does not guarantee it.
#
# No Node. package.json is the JavaScript test runner (`npm test`), not a
# runtime dependency -- no Node file is executed by this service, and the
# frontend is served as static bytes by FastAPI, not built.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Container stdout is a pipe, not a tty, so Python block-buffers and the log
# shows up in chunks minutes late -- precisely when you need it. This is the
# container equivalent of the unit's PYTHONUNBUFFERED=1.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WORKDIR must be the repository ROOT, not the package. backend/config.py:154
# derives BASE_DIR from __file__ (the file's parent's parent), so BASE_DIR is
# wherever backend/ was installed -- and CANDIDATE_DIR, WIKI_DIR, AUDIO_DIR,
# FRONTEND_DIR, REPORTS_DIR, DB_PATH and the frontend mount at "/" are all
# BASE_DIR-relative. Get this wrong and every path resolves somewhere else.
WORKDIR /app

# torch from the CPU wheel index, installed and pinned in its own layer BEFORE
# the project, for two reasons.
#
# 1. `pip install sentence-transformers` resolves torch from PyPI, where the
#    default Linux wheel is the CUDA build and drags in the nvidia-* stack --
#    multiple GB to download, and every byte of it dead weight for a service
#    configured WHISPER_DEVICE=cpu on a laptop.
# 2. Once torch is satisfied, the project install below does not re-resolve it,
#    so the resolver is not offered the CUDA candidate a second time.
#
# --extra-index-url, NOT --index-url, and that distinction is not cosmetic. The
# PyTorch index does not mirror every distribution, so making it the ONLY index
# fails the build on a build-time dependency it does not carry -- the first
# attempt at this line died on `flit_core<4,>=3.11` before it reached torch.
# As an EXTRA index, PyPI still serves everything else and PEP 440's local
# version ordering does the rest: 2.14.1+cpu sorts above the plain 2.14.1 on
# PyPI, so the CPU wheel wins the comparison without any pinning.
#
# Its own RUN so that a pyproject.toml edit does not invalidate a 196 MB layer.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch

# The install needs both of the COPYs below to have happened first: the editable
# build resolves the `backend` package by walking the tree, so pyproject.toml
# alone would install an empty distribution.
COPY pyproject.toml ./
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# pyproject.toml is the manifest, not backend/requirements.txt. It is the one
# with [build-system] and [tool.setuptools.packages.find], without which pip
# falls back to setuptools flat-layout discovery and aborts: this repository has
# 14 top-level directories and the finder refuses to guess which are packages.
# tests/test_packaging.py also holds the two manifests to identical distribution
# sets and version floors, so reading either gives the same install -- but only
# pyproject.toml is installable.
#
# Editable, deliberately. This is the load-bearing choice in this file:
#
#   A NON-editable `pip install .` copies backend/ into site-packages, and then
#   `import backend` resolves there -- so BASE_DIR becomes site-packages/..,
#   FRONTEND_DIR stops existing, the `if config.FRONTEND_DIR.exists()` guard at
#   backend/main.py:333 silently skips the "/" mount, and the service answers
#   404 at the root with a green health endpoint. Editable keeps /app on
#   sys.path, so BASE_DIR is /app and the frontend is where the code expects it.
#
# No [dev] extra: pytest is what CI needs, not what a running service needs.
# (The `tomli` marker for Python 3.10 is dev-only, and 3.10 is this image's
# interpreter -- so the extra is dead weight here.)
RUN pip install --no-cache-dir -e .

# Directories the app writes to, created here so the image boots even if a
# volume is missing, and chowned so a named volume mounted over one of them
# INHERITS this ownership.
#
# That inheritance is the whole reason /app/.cache/huggingface and
# /app/backend/.rag_cache are created and chowned in the image rather than left
# to Docker: a named volume over a path that does not exist is created root-owned
# (mode 0755) and a non-root service cannot write it, which is how the HF cache
# turns into a re-download of 457 MB on every single `up`. Over a path that
# exists, Docker seeds the volume from the image and preserves the owner.
#
# /app/audio must exist for a different reason: backend/main.py:325 mounts
# StaticFiles on it at IMPORT time, and StaticFiles refuses to mount a missing
# directory -- so without this the process would die on import, before any
# error could be logged.
RUN groupadd --gid 10001 interviewtts \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/interviewtts \
            --shell /usr/sbin/nologin interviewtts \
 && mkdir -p /app/.cache/huggingface /app/backend/.rag_cache \
             /app/audio /app/data /app/reports \
 && chown -R interviewtts:interviewtts /app

# Imitates User=interviewtts. Nothing in this image needs to write outside the
# five directories chowned above.
USER interviewtts

# /api/health, with a python one-liner rather than curl: the interpreter is
# already here, and curl would be a second package in the image for one line.
#
# start-period is 900s and it is load-bearing. The first boot is not slow because
# the app is slow, it is slow because HuggingFace has to fetch the models THEN:
# the embedder (~457 MB) during RAGPipeline.initialize() and Whisper during
# STTService.load_model(), both before the ASGI server can answer anything.
# Docker kills a container that fails its healthcheck, so a start-period
# calibrated to a warm cache turns the first cold start into a restart loop.
#
# Note what this asserts: REACHABILITY, not correctness. /api/health returns 200
# even when the body says status:"degraded", and that is correct -- it reports
# `rag_mode` and `problems` precisely so that a retrieval pipeline which fell
# back to TF-IDF is visible while the process is still serving. Gating liveness
# on that would restart a healthy process over a degraded retrieval mode and
# hide the very fact the endpoint exists to surface.
HEALTHCHECK --interval=30s --timeout=15s --start-period=900s --retries=3 \
    CMD python -c "import sys,urllib.request as u; \
sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/health', timeout=10).status == 200 else 1)" \
 || exit 1

# deployment/interviewtts.service:24, with one substitution: --host 0.0.0.0
# instead of 127.0.0.1. Inside a container, loopback is the container's own
# namespace -- binding it would make the service unreachable from outside the
# container no matter what the port publish says. The isolation that
# --host 127.0.0.1 was providing is done at the publish instead
# (docker-compose.yml binds the host side to loopback).
#
# --proxy-headers and --forwarded-allow-ips are kept verbatim from the unit. With
# the allow-list left at 127.0.0.1 they are inert for anything that is not
# loopback, so no client off this host can forge X-Forwarded-For past the
# per-IP rate limiter; widening it to "*" would be the vulnerability the unit's
# comment at line 21 warns about.
CMD ["uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]