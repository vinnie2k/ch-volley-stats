FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
EXPOSE 8000
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000", "--update-every", "60"]
