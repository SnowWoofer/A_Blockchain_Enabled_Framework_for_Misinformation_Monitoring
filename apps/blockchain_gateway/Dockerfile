FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY app/v1-0-0/api/requirements.txt .
RUN pip install -r requirements.txt

COPY app/v1-0-0/api ./api
COPY app/v1-0-0/src ./src

EXPOSE 8000

# v3 uses authenticated per-organisation SDK signers, or the explicitly named
# SQLite reference backend for comparison. No peer CLI or private keys belong
# in the workflow API image.
CMD ["uvicorn", "--app-dir", "api", "server:app", "--host", "0.0.0.0", "--port", "8000"]
