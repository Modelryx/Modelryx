# Update the release tag and verified multi-platform digest together.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY server/pyproject.toml server/README.md server/LICENSE ./
COPY server/src/server ./src/server
COPY worker-sdk /tmp/worker-sdk
RUN pip install --no-cache-dir /tmp/worker-sdk && pip install --no-cache-dir .

RUN groupadd --system --gid 101 modelryx \
    && useradd --system --uid 100 --gid 101 --create-home modelryx \
    && mkdir -p /var/lib/modelryx/objects \
    && chown -R modelryx:modelryx /var/lib/modelryx

FROM base AS test
RUN pip install --no-cache-dir ".[dev]"
COPY server/tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
USER modelryx

EXPOSE 8000
CMD ["uvicorn", "modelryx.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
