# stop.ps1
# Stops Redis, API, and Celery worker. Ollama is left running natively
# since Docker Compose doesn't manage it - stop it manually (close its
# terminal, or Ctrl+C) if you want to free up RAM.

Write-Host "Stopping Docker services (Redis, API, Celery worker)..." -ForegroundColor Cyan
docker compose down

Write-Host ""
Write-Host "Done. Note: Ollama is still running natively." -ForegroundColor Yellow
Write-Host "Close its terminal window (or Ctrl+C) if you want to free up RAM."