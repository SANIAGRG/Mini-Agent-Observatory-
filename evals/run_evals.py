import json
import os
import re
import sqlite3
import time
import uuid
import sys
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_PATH = os.path.join(HERE, "dataset.json")
CONFIG_PATH = os.path.join(HERE, "config.json")

# traces.db lives in ../api relative to this script by default, since that's
# where the FastAPI/Celery containers read and write it from.
DB_PATH = os.environ.get("EVAL_DB_PATH", os.path.join(HERE, "..", "api", "traces.db"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.2")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_eval_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            eval_run_id TEXT,
            case_id TEXT,
            type TEXT,
            passed INTEGER,
            score REAL,
            reason TEXT,
            latency_ms INTEGER,
            created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def log_eval_result(eval_run_id, case_id, type_, passed, score, reason, latency_ms):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO eval_runs
            (id, eval_run_id, case_id, type, passed, score, reason, latency_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            eval_run_id,
            case_id,
            type_,
            int(passed) if passed is not None else None,
            score,
            reason,
            latency_ms,
            time.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def run_agent(prompt, api_base_url, poll_interval, poll_timeout):
    """Sends the prompt through the real, live API - same path a real user
    request takes - with caching explicitly bypassed."""
    start = time.perf_counter()
    resp = requests.post(
        f"{api_base_url}/agent/run",
        json={"prompt": prompt, "bypass_cache": True},
        timeout=30,
    )
    resp.raise_for_status()
    task_id = resp.json()["task_id"]

    waited = 0
    while waited < poll_timeout:
        status_resp = requests.get(f"{api_base_url}/agent/status/{task_id}", timeout=30)
        status_resp.raise_for_status()
        data = status_resp.json()

        if data["status"] == "success":
            latency_ms = int((time.perf_counter() - start) * 1000)
            return data["result"]["response"], latency_ms
        if data["status"] == "failure":
            raise RuntimeError(f"Agent task failed: {data.get('error')}")

        time.sleep(poll_interval)
        waited += poll_interval

    raise TimeoutError(f"Agent task {task_id} did not finish within {poll_timeout}s")


def deterministic_check(response, case):
    check = case["check"]
    if check == "contains":
        return case["expected_substring"].lower() in response.lower()
    if check == "valid_json":
        try:
            json.loads(response)
            return True
        except Exception:
            return False
    raise ValueError(f"Unknown deterministic check type: {check}")


def judge_response(prompt, response, rubric):
    """A second, separate Ollama call, acting as a grader rather than the
    agent itself."""
    judge_prompt = (
        "You are grading an AI assistant's response.\n\n"
        f"User's question: {prompt}\n\n"
        f"Assistant's response: {response}\n\n"
        f"Grading rubric: {rubric}\n\n"
        "Score the response from 1 to 5, where 5 fully meets the rubric and "
        "1 does not meet it at all. Reply in exactly this format:\n"
        "SCORE: <number>\n"
        "REASON: <one sentence>"
    )
    payload = {"model": MODEL_NAME, "prompt": judge_prompt, "stream": False}
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    text = resp.json().get("response", "")

    score_match = re.search(r"SCORE:\s*(\d+(?:\.\d+)?)", text)
    reason_match = re.search(r"REASON:\s*(.+)", text)

    score = float(score_match.group(1)) if score_match else None
    reason = reason_match.group(1).strip() if reason_match else text.strip()[:200]

    return score, reason


def main():
    config = load_json(CONFIG_PATH)
    dataset = load_json(DATASET_PATH)
    ensure_eval_table()

    eval_run_id = str(uuid.uuid4())
    print(f"Eval run: {eval_run_id}")
    print(f"Running {len(dataset)} test case(s) against {config['api_base_url']}")
    print("This bypasses the cache, so expect real inference time on every case.\n")

    deterministic_results = []
    judge_scores = []
    all_calls_succeeded = True

    for case in dataset:
        print(f"- [{case['id']}] sending prompt...")
        try:
            response, latency_ms = run_agent(
                case["prompt"],
                config["api_base_url"],
                config["poll_interval_seconds"],
                config["poll_timeout_seconds"],
            )
        except Exception as e:
            print(f"  FAILED to get a response: {e}")
            log_eval_result(eval_run_id, case["id"], case["type"], False, None, str(e), None)
            all_calls_succeeded = False
            continue

        if case["type"] == "deterministic":
            passed = deterministic_check(response, case)
            deterministic_results.append(passed)
            print(f"  deterministic check: {'PASS' if passed else 'FAIL'}  (latency {latency_ms}ms)")
            log_eval_result(eval_run_id, case["id"], "deterministic", passed, None, None, latency_ms)

        elif case["type"] == "judge":
            score, reason = judge_response(case["prompt"], response, case["rubric"])
            judge_scores.append(score if score is not None else 0)
            print(f"  judge score: {score}  ({reason})  (latency {latency_ms}ms)")
            log_eval_result(eval_run_id, case["id"], "judge", None, score, reason, latency_ms)

    print("\n--- Summary ---")

    det_pass_rate = (sum(deterministic_results) / len(deterministic_results)) if deterministic_results else 1.0
    avg_judge_score = (sum(judge_scores) / len(judge_scores)) if judge_scores else 5.0

    print(f"Deterministic pass rate: {det_pass_rate:.2f}  (threshold: {config['deterministic_pass_rate']})")
    print(f"Average judge score:     {avg_judge_score:.2f}  (threshold: {config['min_judge_score']})")

    gate_passed = (
        all_calls_succeeded
        and det_pass_rate >= config["deterministic_pass_rate"]
        and avg_judge_score >= config["min_judge_score"]
    )

    print(f"\nOVERALL: {'PASS' if gate_passed else 'FAIL'}")

    if not gate_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
