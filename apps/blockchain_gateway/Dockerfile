FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY app/v1-0-0/api/requirements.txt .
RUN pip install -r requirements.txt

COPY app/v1-0-0/api ./api
COPY app/v1-0-0/src ./src

EXPOSE 8000

# The peer-CLI fallback bridge (FabricBridge) needs the `peer` binary and
# fabric-samples/organizations on PATH/disk, which this image deliberately
# doesn't bundle — containerized, this service only runs with
# FABRIC_BACKEND=gateway (the official SDK sidecar, apps/fabric_gateway),
# never the CLI fallback. Run it directly on the host (see README) if you
# need the CLI fallback path.
CMD ["uvicorn", "--app-dir", "api", "server:app", "--host", "0.0.0.0", "--port", "8000"]
