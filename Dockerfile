FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    DB_PATH=/data/tender_radar.db

WORKDIR /app
COPY . /app

VOLUME ["/data"]
EXPOSE 8080

CMD ["python", "-m", "tender_radar.cli", "serve"]
