# Single Dockerfile for both profiles. torch itself is installed separately so
# the CPU/CUDA wheel choice is a build arg instead of two Dockerfiles:
#
#   Laptop test (cpu):  docker build -t flagging-engine:cpu .
#   5090 server (cuda): docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 \
#                          -t flagging-engine:cuda .
#
# The cuda wheel bundles its own CUDA runtime libs (nvidia-*-cu12 deps), so no
# nvidia/cuda base image is needed - just run the cuda-built image with
# `docker run --gpus all` on a host with the NVIDIA driver + container toolkit.
FROM python:3.11-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_PATH=/app/model

WORKDIR /app

RUN pip install torch --index-url ${TORCH_INDEX_URL}

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY model ./model

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
