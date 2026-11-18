FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py virtual_queue.py anti_scalping.py ./

USER 10001
EXPOSE 8000
CMD ["python", "-u", "server.py"]
