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
RUN python -m pip install --no-cache-dir -c requirements.lock '.[ui,bedrock]'
COPY .streamlit ./.streamlit
RUN mkdir runs && chown research:research runs

USER research
EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)"
CMD ["streamlit", "run", "src/financial_research/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--server.fileWatcherType=none"]
