FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend ./backend
COPY assets ./assets
COPY reports ./reports
COPY VERSION index.html demo.html study.html ./

RUN useradd --create-home --uid 10001 tropos \
    && mkdir -p /data \
    && chown -R tropos:tropos /data /app

USER tropos
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).read()"]

CMD ["python", "-m", "backend.server", "--host", "127.0.0.1", "--port", "8765", "--db", "/data/tropos.sqlite3"]
