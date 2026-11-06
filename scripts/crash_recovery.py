"""Kill the controller mid-run, restart it, and verify the HTTP world finishes.

Run with: python scripts/crash_recovery.py
The harness may inspect simulator state for verification; controller code only
uses the documented HTTP API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / f"crash_recovery_{int(time.time())}"
URL = "http://127.0.0.1:8799"


def launch(args: list[str], out: Path, err: Path, env=None):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, *args], cwd=ROOT, env=env,
        stdout=out.open("w"), stderr=err.open("w"),
        creationflags=flags, start_new_session=os.name != "nt",
    )


def stop(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=15)


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    world_dir = RUN / "world"
    server = launch(
        ["-m", "sim", "serve", "--scenario", "scenarios/public_01_dev.yaml",
         "--run-dir", str(world_dir), "--port", "8799"],
        RUN / "server.out", RUN / "server.err",
    )
    controller = None
    client = httpx.Client(base_url=URL, timeout=15)
    try:
        for _ in range(600):
            try:
                if client.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("simulator did not start")

        env = os.environ.copy()
        env["CONTROLLER_DB"] = str(RUN / "controller.db")
        controller = launch(["main.py", URL], RUN / "first.out", RUN / "first.err", env)
        killed_at = None
        for _ in range(1000):
            state = client.get("/sim/state").json()
            if state["sim_minute"] >= 660:
                killed_at = state["sim_minute"]
                break
            if controller.poll() is not None:
                raise RuntimeError("controller exited before kill")
            time.sleep(0.02)
        if killed_at is None:
            raise RuntimeError("controller did not reach D0 11:00")
        stop(controller)
        before = client.get("/sim/state").json()
        controller = launch(["main.py", URL], RUN / "second.out", RUN / "second.err", env)
        controller.wait(timeout=300)
        if controller.returncode:
            raise RuntimeError(f"restarted controller exited {controller.returncode}")
        after = client.get("/sim/state").json()
        if not after["finished"]:
            raise AssertionError("simulator did not finish")
        approvals = client.get("/approvals").json()
        if any(item["status"] in ("pending", "approved") for item in approvals):
            raise AssertionError("an approval was left unresolved")
        with sqlite3.connect(RUN / "controller.db") as db:
            cursor_row = db.execute(
                "SELECT value FROM checkpoints WHERE key='last_processed_event_id'"
            ).fetchone()
        committed_event_id = json.loads(cursor_row[0]) if cursor_row else -1
        if committed_event_id != after["last_event_id"]:
            raise AssertionError("controller did not ingest the final event")
        histories = {wf["workflow_id"]: client.get(
            f"/workflows/{wf['workflow_id']}/config/history").json()
            for wf in client.get("/workflows").json()}
        proposal_ids = [entry["proposal_id"] for history in histories.values()
                        for entry in history if entry["proposal_id"]]
        if len(proposal_ids) != len(set(proposal_ids)):
            raise AssertionError("a proposal was applied more than once")
        result = {
            "killed_at_minute": killed_at,
            "restart_state_minute": before["sim_minute"],
            "finished_at_minute": after["sim_minute"],
            "finished": after["finished"],
            "approvals": len(approvals),
            "unresolved_approvals": 0,
            "applied_proposal_ids_unique": True,
            "last_event_id": after["last_event_id"],
            "committed_event_id": committed_event_id,
        }
        (RUN / "evidence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        if controller:
            stop(controller)
        stop(server)
        client.close()


if __name__ == "__main__":
    main()
