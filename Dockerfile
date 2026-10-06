FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATA_DIR=/data \
    ADMIN_PORT=8000 \
    PREVIEW_PORT=8001
VOLUME /data
EXPOSE 8000 8001

CMD ["python", "-m", "app.main"]
