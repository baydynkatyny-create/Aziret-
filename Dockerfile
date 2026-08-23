FROM python:3.11-slim

# ffmpeg is required by main.py (audio/video/cartoon endpoints) and is
# not installed by default in the slim image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets $PORT at runtime; fall back to 8000 for local runs.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
