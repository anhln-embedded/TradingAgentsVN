FROM python:3.11-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY tradingagents ./tradingagents
COPY cli ./cli
COPY web/requirements.txt ./web/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir . -r web/requirements.txt

FROM python:3.11-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TA_ENV=production \
    TA_DATA_DIR=/data \
    XDG_CACHE_HOME=/data/cache/xdg \
    TA_RUNTIME_ENV_FILE=/data/config/runtime.env \
    TA_ALLOW_GIT_UPDATES=false
ARG APP_REVISION=unknown
LABEL org.opencontainers.image.revision=$APP_REVISION
COPY --from=builder /opt/venv /opt/venv
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /data/config /data/results /data/cache /data/memory \
    && chown -R appuser:appuser /data
WORKDIR /app
COPY --chown=appuser:appuser tradingagents ./tradingagents
COPY --chown=appuser:appuser vnapp ./vnapp
COPY --chown=appuser:appuser web ./web
USER appuser
EXPOSE 8000
CMD ["uvicorn", "web.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "300", "--no-server-header"]
