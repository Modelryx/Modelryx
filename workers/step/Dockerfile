# Update the release tag and verified multi-platform digest together.
FROM condaforge/miniforge3:25.9.1-0@sha256:7ddae459669444129323a431fed8959e8972700dfb43c790d22abaeab85ff9d1
COPY environment.yml /tmp/environment.yml
RUN conda env create -f /tmp/environment.yml && conda clean --all -y
WORKDIR /app
COPY app /app/app
COPY converter /app/converter
COPY tests /app/tests
RUN useradd --system --uid 10001 --create-home converter && mkdir -p /data/jobs && chown -R converter:converter /data
USER converter
ENV PATH=/opt/conda/envs/step-worker/bin:$PATH \
    STEP_WORKER_DATA_ROOT=/data/jobs \
    STEP_WORKER_QUEUE_CAPACITY=32 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ready')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
