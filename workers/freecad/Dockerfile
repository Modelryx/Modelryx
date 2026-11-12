# Update the release tag and verified multi-platform digest together.
FROM condaforge/miniforge3:25.9.1-0@sha256:7ddae459669444129323a431fed8959e8972700dfb43c790d22abaeab85ff9d1

ARG MODELRYX_FREECAD_VERSION=1.1.3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN conda create -n modelryx-worker -y -c conda-forge python=3.11 "freecad=${MODELRYX_FREECAD_VERSION}" numpy trimesh pillow \
    && conda clean --all -y
ENV PATH=/opt/conda/envs/modelryx-worker/bin:$PATH \
    PYTHONPATH=/opt/conda/envs/modelryx-worker/lib \
    LD_LIBRARY_PATH=/opt/conda/envs/modelryx-worker/lib

WORKDIR /app
COPY server/pyproject.toml server/README.md server/LICENSE ./
COPY server/src/server ./src/server
COPY worker-sdk /tmp/worker-sdk
COPY workers/freecad/converter /opt/modelryx-converter
COPY workers/freecad/preprocessor /opt/modelryx-preprocessor
COPY workers/freecad/shared /opt/shared
COPY workers/freecad/modelryx_freecad_adapter.py /opt/modelryx-worker/
COPY workers/freecad/tests/process_tree_integration_test.py /opt/modelryx-converter/
RUN /opt/conda/envs/modelryx-worker/bin/python \
    /opt/modelryx-converter/fcstd_to_modelryx_glb.py --help >/dev/null
RUN /opt/conda/envs/modelryx-worker/bin/pip install --no-cache-dir /tmp/worker-sdk \
    && /opt/conda/envs/modelryx-worker/bin/pip install --no-cache-dir .

RUN groupadd --system --gid 101 modelryx \
    && useradd --system --uid 100 --gid 101 --create-home modelryx \
    && mkdir -p /var/lib/modelryx/objects \
    && chown -R modelryx:modelryx /var/lib/modelryx

ENV PYTHONPATH=/opt/modelryx-worker:/opt/conda/envs/modelryx-worker/lib \
    MODELRYX_CAD_WORKER_ADAPTER=modelryx_freecad_adapter \
    MODELRYX_PROCESSING_WORKER_ROLE=freecad

USER modelryx
CMD ["python", "-m", "modelryx.services.processing"]
