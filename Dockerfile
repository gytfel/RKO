FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py collector.py db.py scoring.py ./
COPY static/ ./static/
COPY templates/ ./templates/
RUN mkdir -p /app/data && useradd -r -u 10001 appuser && chown appuser:appuser /app/data
USER appuser
EXPOSE 8080
CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "60", "--bind", "0.0.0.0:8080", "app:app"]
