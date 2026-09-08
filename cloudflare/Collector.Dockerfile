FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 TZ=Asia/Seoul
WORKDIR /app
# Only application modules: never COPY .env, production DB, or Git history.
COPY tender_radar /app/tender_radar
EXPOSE 8080
CMD ["python", "-m", "tender_radar.isolated_collector"]
