#!/bin/bash
# start.sh
# One-command startup: makes sure Ollama is running on the host,
# then starts Redis + API + Celery worker via Docker Compose.

echo "Checking for Ollama..."
if curl -s http://localhost:11434 > /dev/null; then
    echo "Ollama is already running."
else
    echo "Ollama not detected — starting it now..."
    nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 5
    echo "Ollama started."
fi

echo "Starting Redis, API, and Celery worker via Docker Compose..."
docker compose up -d --build

echo ""
echo "All set. API is available at http://localhost:8000"
echo "Check logs anytime with: docker compose logs -f"
