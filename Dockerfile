# Stage 1 builds the React desk. Only its compiled files reach the final image.
FROM node:22-slim AS frontend
WORKDIR /frontend
# Install from the lockfile before copying sources so edits reuse the cached dependencies.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# Stage 2 runs FastAPI, which serves both /api and the built React files.
FROM python:3.14-slim

ARG APP_REVISION=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RESEARCH_REVISION=$APP_REVISION
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 research

COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN python -m pip install --no-cache-dir -c requirements.lock '.[web,bedrock,news]'
COPY --from=frontend /frontend/dist ./frontend/dist
# Releases are extracted with a private umask; the app user must still read its code.
RUN chmod -R a+rX src frontend \
    && mkdir runs && chown research:research runs

USER research
EXPOSE 8000
# Requesting the page checks that Uvicorn is up and found the React build.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/', timeout=2)"
# Keep Uvicorn's default single worker: web.py tracks the active run in process memory.
CMD ["uvicorn", "financial_research.web:app", "--host", "0.0.0.0", "--port", "8000"]
