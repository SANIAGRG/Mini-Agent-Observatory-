# start.ps1
# One-command startup: makes sure Ollama is running on the host,
# then starts Redis + API + Celery worker via Docker Compose.

Write-Host "Checking for Ollama..." -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri http://localhost:11434 -TimeoutSec 2 | Out-Null
    Write-Host "Ollama is already running." -ForegroundColor Green
} catch {
    Write-Host "Ollama not detected - starting it now..." -ForegroundColor Yellow
    Start-Process -WindowStyle Hidden ollama -ArgumentList "serve"
    Start-Sleep -Seconds 5
    Write-Host "Ollama started." -ForegroundColor Green
}

Write-Host "Starting Redis, API, and Celery worker via Docker Compose..." -ForegroundColor Cyan
docker compose up -d --build

Write-Host ""
Write-Host "All set. API is available at http://localhost:8000" -ForegroundColor Green
Write-Host "Check logs anytime with: docker compose logs -f"