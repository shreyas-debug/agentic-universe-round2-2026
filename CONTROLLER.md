# Controller operations

Python 3.10+ and the dependencies in `requirements.txt` are required. On Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base01
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01
# In another terminal:
CONTROLLER_DB=controller.db ./run.sh http://127.0.0.1:8700
python -m sim grade --run runs/mine01 --baseline runs/base01 --out reports/public_01.md
```

`run.sh` is the grader entry point. It runs until `/sim/state.finished` and accepts only the simulator URL. The optional `CONTROLLER_DB` path identifies a durable SQLite file. Preserve that file when restarting against the same live simulator. Use a fresh path for independent runs. The controller detects a different scenario or a simulator clock that moved backwards and clears old local state.

## Inspect a decision

```bash
python main.py audit --workflow ks_cancel --at 5200 --db controller.db
```

The command shows actions before and after the requested simulated minute. `audit_log` holds proposals, approvals, supervisor edits, versions and rationale. `proposals` holds the durable request intent and terminal status. `loop_metrics` holds per-tick event count, ingestion time, tick time and credits remaining. To inspect a metric from SQLite:

```sql
SELECT sim_minute, events_ingested, ingest_seconds, tick_seconds, credits_remaining
FROM loop_metrics ORDER BY sim_minute DESC LIMIT 5;
```

## Recovery and failure response

Run `python scripts/crash_recovery.py` to create a fresh local simulator run, kill the controller after D0 11:00, restart it and verify completion, final event cursor and unique applied proposal IDs. The harness uses the documented HTTP API and may inspect its own SQLite file; the controller never reads scenario files or simulator run directories.

If the controller stops on HTTP 5xx or a network failure, keep the simulator and database alive and restart `run.sh` with the same URL and `CONTROLLER_DB`. It drains unseen event chunks before advancing. An approved proposal near its apply deadline needs immediate attention. A 4xx policy failure is recorded and not forced through. A supervisor edit makes an old base version stale; a new proposal is considered in a later decision window.

No external daemon is required. The only stateful controller dependency is SQLite. In production, replace the single local writer with a shared transactional database and workflow leases before running multiple controller replicas.
