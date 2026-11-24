# Publisher image: no ffmpeg/rendering, only upload + validation. Build from the repo root:
#   gcloud run deploy ytauto-publisher --source . ...   (see docs/deployment.md)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-publisher.txt .
RUN pip install -r requirements-publisher.txt && useradd --uid 10001 --no-create-home app
COPY src/ ./src/
COPY cloudrun/service.py ./cloudrun/service.py
USER 10001
ENV PYTHONPATH=/app/src
# One worker, one request at a time: uploads are sequential and memory stays small.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 1 --timeout 900 cloudrun.service:app
