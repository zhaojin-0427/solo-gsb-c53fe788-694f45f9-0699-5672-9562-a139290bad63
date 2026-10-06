FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests

ENV DATA_DIR=/data \
    ADMIN_PORT=8000 \
    REPLAY_PORT=8001

VOLUME /data
EXPOSE 8000 8001

CMD ["python", "-m", "app.main"]
