FROM python:3.12.12-slim-bookworm AS build
WORKDIR /build
RUN pip install --no-cache-dir uv==0.8.22
COPY requirements.txt .
RUN uv venv --python /usr/local/bin/python /opt/venv && \
    uv pip sync --python /opt/venv/bin/python --require-hashes --only-binary :all: \
    --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match requirements.txt

FROM python:3.12.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libnuma1 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 10001 app && useradd -u 10001 -g 10001 -m app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY app/start.py app/privacy.py app/sitecustomize.py ./
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 HOME=/home/app VLLM_TARGET_DEVICE=cpu \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "/app/start.py"]
