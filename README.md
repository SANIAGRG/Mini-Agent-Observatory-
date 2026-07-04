# Mini Agent Observatory

A 100% free, fully local system for running an LLM agent, tracing every
call it makes, caching repeated work, reasoning in multiple steps, and
being automatically graded for quality and safety on every code change -
all running on your own machine, with no API keys and no ongoing cost.

---

## What this actually is

Most small AI projects stop at "I called a model and got a response."
This one is built around a different question: **how do you know an AI
system is behaving correctly, staying fast, and not regressing over
time** - without a human manually checking every time?

That question is answered here with:

- **Tracing** - every LLM call logged with latency, token counts, and cache status
- **Caching** - identical requests skip inference entirely
- **Live dashboards** - Grafana, reading directly from the trace data
- **Multi-step reasoning** - the agent plans, then answers, not one-shot
- **Automated evaluation** - a golden dataset scored both deterministically and by an LLM acting as a judge, including a safety-refusal check
- **CI enforcement** - the eval suite runs on every push and blocks a build if the agent's behavior regresses
- **Production hardening** - health checks, automatic retries, structured logging, memory limits

---

## Prerequisites

### Required

- **Ollama** - the local LLM runtime everything else depends on.
  - Install: https://ollama.com/download
  - Pull the model used by default:
    ```
    ollama pull llama3.2
    ```
  - Ollama can be left running natively; the startup script below will
    start it automatically if it isn't already running.

- **Docker Desktop** (with Docker Compose) - runs Redis, the API, the
  Celery worker, and Grafana.
- **Python 3.10+** - only needed if you want to run the eval harness
  directly (`pip install requests` is its only dependency).
- **Git**.

### Optional

- A GitHub account with Actions enabled, if you want the CI eval gate to
  run remotely on every push (already configured in this repo).

---

## Quick start (one command)

```powershell
# Windows
.\start.ps1
```
```bash
# macOS / Linux
./start.sh
```

This script:
1. Checks whether Ollama is already running; starts it if not.
2. Runs `docker compose up -d --build`, which brings up Redis, the API,
   the Celery worker, and Grafana - each waiting on the previous one's
   **health check** to actually pass, not just "container started."

Once it finishes:
- API: http://localhost:8000
- Grafana dashboard: http://localhost:3000

To shut down the Docker-managed services (Ollama is left running natively):
```powershell
.\stop.ps1
```

### Manual test

```powershell
$job = Invoke-RestMethod -Uri http://localhost:8000/agent/run -Method Post `
  -ContentType "application/json" -Body '{"prompt": "what is 5 times 6"}'

# job.task_id returns instantly - the agent runs in the background
Invoke-RestMethod -Uri "http://localhost:8000/agent/status/$($job.task_id)"
```

---

## Architecture

```
Client
  |  POST /agent/run  { prompt, bypass_cache }
  v
FastAPI (api)  --enqueues job, returns task_id instantly-->  Redis (queue, db 0)
                                                                  |
                                                                  v
                                                          Celery worker
                                                    Step 1: plan  --\
                                                    Step 2: answer --/  (cached via Redis db 1)
                                                                  |
                                                                  v
                                              Ollama (native host, via host.docker.internal)
                                                                  |
                                                                  v
                                              SQLite (traces.db) - every step logged
                                                                  |
                                                                  v
                                                     Grafana - live dashboards

On every git push: GitHub Actions rebuilds this entire stack from scratch
on a disposable machine, runs the eval suite against it, and only allows
a build to proceed if the agent's behavior still passes.
```

Ollama deliberately runs **natively**, not in Docker - containers cannot
start a process on the host OS, and containerizing Ollama would mean
duplicating multi-gigabyte model weights inside an image. The container
side reaches it via `host.docker.internal`.

---

## Tech stack

| Tool | Role |
|---|---|
| **Ollama** | Runs the LLM (llama3.2) locally - no API key, no cost |
| **FastAPI** | The HTTP entry point - validates requests, enqueues jobs |
| **Redis** | Task queue/result backend (db 0) *and* response cache (db 1), on one container |
| **Celery** | Background worker - runs the actual 2-step agent logic asynchronously |
| **SQLite** | Stores every trace and eval result (`traces.db`) - no separate DB server needed |
| **Docker + Compose** | Containerizes Redis, the API, the worker, and Grafana behind one startup command |
| **Grafana** | Live dashboards built directly against `traces.db` via a community SQLite plugin |
| **GitHub Actions** | Runs the eval suite on every push and gates whether a build proceeds |

---

## The agent itself

Every request runs two LLM calls, not one, sharing a `run_id`:

1. **Plan** - the model outlines its approach in 2-3 bullets, without answering yet
2. **Answer** - the plan is fed back in as context, and the model gives the final answer

Both steps are individually traced (`step: 1` / `step: 2`), timed, and
cache-checked independently.

---

## Evaluation

An agent that has never been evaluated is a demo, not a system. The eval
harness (`evals/`) runs a small golden dataset through the **real, live
API** - the same path a genuine request takes - and scores it two ways:

| Tier | How it scores | Threshold |
|---|---|---|
| Deterministic | Exact match (e.g. does the answer contain the right fact) | 100% pass rate |
| LLM-as-judge | A second Ollama call grades the first one 1-5 against a rubric | Average >= 3.5 / 5 |

The 3 test cases include a basic factual check, a **safety-refusal
check** (does the agent decline to give break-in instructions), and a
general helpfulness/focus check.

**Evals always bypass the cache** (`bypass_cache: true`), so a repeat
eval run never silently re-scores a stale, pre-change answer.

Run it manually:
```bash
python evals/run_evals.py
```
Expect this to take several minutes - two agent calls plus a judge call
per case, on CPU-only hardware.

Results are written to an `eval_runs` table in `traces.db`, alongside
regular trace data.

---

## Continuous integration

Every push triggers `.github/workflows/ci.yml`, which, on a completely
fresh machine:
1. Installs Ollama and pulls a smaller model (`llama3.2:1b`, for CI speed)
2. Builds and starts the full Docker stack
3. Runs the eval suite
4. Only builds the final Docker image if the eval run passed

This means a change that measurably makes the agent worse or less safe
is blocked automatically, before it can be merged - not caught later by
a user.

---

## Production hardening

- **Health checks** on every service (Redis, API, worker, Grafana) -
  `depends_on` waits for genuine readiness, not just "container started"
- **Automatic retries** on transient network failures (e.g. Ollama still
  warming up), with exponential backoff and jitter, capped at 3 attempts
- **Structured JSON logging** from both the API and the worker
- **Memory limits** on every container, so one service can't starve the
  others on constrained hardware

---

## Project structure

```
Mini Agent Observatory/
├── .github/workflows/ci.yml     CI pipeline: installs Ollama, runs evals, gates the build
├── docker-compose.yml            Redis, API, worker, Grafana - networked together
├── start.ps1 / start.sh          One-command startup (checks/starts Ollama, then Docker)
├── stop.ps1                      Stops the Docker-managed services
├── api/
│   ├── main.py                   FastAPI app - /agent/run, /agent/status, /traces
│   ├── tasks.py                  Celery worker - the 2-step agent, caching, retries
│   ├── db.py                     Async SQLite access (FastAPI side)
│   ├── cache.py                  Redis-backed response cache
│   ├── logging_config.py         Shared JSON log formatter
│   ├── Dockerfile
│   ├── requirements.txt
│   └── traces.db                 Trace + eval history (gitignored - runtime data)
├── evals/
│   ├── dataset.json               The golden test set
│   ├── config.json                Pass/fail thresholds
│   └── run_evals.py               The eval harness itself
└── grafana/provisioning/datasources/datasource.yml   Auto-configures the Grafana connection
```

---

## Status

All planned phases are complete: local LLM -> API -> tracing -> async
queue -> containerization -> caching -> dashboards -> multi-step
reasoning -> automated evaluation -> CI enforcement -> production
hardening.

---

## License

Personal learning project - no license restrictions on this repo's own
code. Ollama, FastAPI, Redis, Celery, SQLite, and Grafana are each
governed by their own respective open-source licenses.