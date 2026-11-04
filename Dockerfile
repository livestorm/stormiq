# Exact, patched versions (Node.js October 2026 security release: >= 26.12.1).
# Floating tags let a cached, unpatched base image survive rebuilds.
# Keep the Node version in sync with frontend/.nvmrc.
FROM node:26.13.0-bookworm-slim AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.13.18-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=10000

WORKDIR /app

# requirements.txt is a hashed lock (see its header); every dependency ships
# prebuilt wheels, so no compiler toolchain is needed in the image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY README.md ./
COPY assets ./assets
COPY prompts ./prompts
COPY livestorm_app ./livestorm_app
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Run unprivileged. The app only writes to temporary directories.
RUN useradd --create-home --uid 10001 stormiq
USER stormiq

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"10000\")}/api/health', timeout=4)"

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
