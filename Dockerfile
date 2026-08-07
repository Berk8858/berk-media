FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && pip install --no-cache-dir yt-dlp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/
COPY templates/ /app/templates/

EXPOSE 8084

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8084"]
