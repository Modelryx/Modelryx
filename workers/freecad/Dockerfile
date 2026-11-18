# Update the base image tag and verified digest together.
FROM condaforge/miniforge3:25.9.1-0@sha256:7ddae459669444129323a431fed8959e8972700dfb43c790d22abaeab85ff9d1 AS base

ARG MODELRYX_FREECAD_VERSION=1.1.3
RUN conda create -n modelryx-worker -y -c conda-forge python=3.11 "freecad=${MODELRYX_FREECAD_VERSION}" numpy trimesh pillow \
    && conda clean --all -y
ENV PATH=/opt/conda/envs/modelryx-worker/bin:$PATH \
    PYTHONPATH=/opt/conda/envs/modelryx-worker/lib:/app/converter \
    LD_LIBRARY_PATH=/opt/conda/envs/modelryx-worker/lib \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MODELRYX_CONVERTER_SCRIPT=/app/converter/fcstd_to_modelryx_glb.py \
    MODELRYX_FREECAD_PREPROCESSOR_SCRIPT=/app/preprocessor/modelryx_freecad_preprocessor.py

WORKDIR /app
COPY vendor/worker-sdk /tmp/worker-sdk
COPY standalone/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir /tmp/worker-sdk -r /tmp/requirements.txt
COPY standalone ./standalone
COPY converter ./converter
COPY preprocessor ./preprocessor
COPY shared ./shared
COPY modelryx_freecad_adapter.py ./
RUN python converter/fcstd_to_modelryx_glb.py --help >/dev/null \
    && groupadd --system --gid 101 modelryx \
    && useradd --system --uid 100 --gid 101 --create-home modelryx \
    && mkdir -p /data/jobs \
    && chown -R modelryx:modelryx /data/jobs

FROM base AS native-tests
COPY vendor ./vendor
COPY contracts ./contracts
COPY tests ./tests
COPY scripts/refresh-contracts.py ./scripts/refresh-contracts.py
COPY pyproject.toml ./pyproject.toml
COPY requirements-test.txt ./requirements-test.txt
RUN python -m pip install --no-cache-dir -r requirements-test.txt
ENV PYTHONPATH=/app:/app/converter:/app/shared:/opt/conda/envs/modelryx-worker/lib
USER modelryx
CMD ["python", "converter/golden_integration_test.py"]

FROM base AS runtime
USER modelryx
EXPOSE 8090
CMD ["python", "-m", "uvicorn", "standalone.api:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
