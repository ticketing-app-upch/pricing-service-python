FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py virtual_queue.py ./

RUN mkdir -p /data && chown 10001 /data
ENV VIRTUAL_QUEUE_OUTCOMES_DB=/data/queue-outcomes.sqlite3
VOLUME ["/data"]
USER 10001
EXPOSE 8000
CMD ["python", "-u", "server.py"]
