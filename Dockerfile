FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AUDIBLECALENDAR_DB=/data/audiblecalendar.db \
    AUDIBLECALENDAR_DEMO_DATA=false

WORKDIR /app

RUN groupadd --system --gid 10001 audiblecalendar \
    && useradd --system --uid 10001 --gid audiblecalendar \
        --home-dir /nonexistent --shell /usr/sbin/nologin audiblecalendar \
        && mkdir -p /data \
    && chown audiblecalendar:audiblecalendar /data

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY static ./static
COPY assets ./assets
COPY LICENSE ./LICENSE

RUN chown -R audiblecalendar:audiblecalendar /app

USER audiblecalendar

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
