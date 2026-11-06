"""HTTP Client for Formi Simulator.

Handles communication with the simulator over HTTP with timeout and error handling.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator
import httpx

logger = logging.getLogger(__name__)


class SimClient:
    """Wrapper around HTTP API exposed by the simulator."""

    def __init__(self, base_url: str = "http://127.0.0.1:8700", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def wait_for_ready(self, timeout_s: float = 30.0):
        """Wait until simulator HTTP server is ready and responding to /health."""
        import time
        start = time.time()
        while time.time() - start < timeout_s:
            try:
                resp = self.client.get("/health")
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"Simulator at {self.base_url} did not become ready within {timeout_s}s")

    def health(self) -> dict[str, Any]:
        resp = self.client.get("/health")
        resp.raise_for_status()
        return resp.json()

    def get_state(self) -> dict[str, Any]:
        resp = self.client.get("/sim/state")
        resp.raise_for_status()
        return resp.json()

    def advance(self, minutes: int = 15) -> dict[str, Any]:
        resp = self.client.post("/sim/advance", json={"minutes": minutes})
        if resp.status_code == 409:
            # Simulation finished
            return {"finished": True, "new_chunks": []}
        resp.raise_for_status()
        return resp.json()

    def list_chunks(self, kind: str = "events", after: int = -1) -> list[dict[str, Any]]:
        resp = self.client.get("/events/chunks", params={"kind": kind, "after": after})
        resp.raise_for_status()
        return resp.json()

    def get_chunk_lines(self, chunk_id: int) -> Iterator[str]:
        # Avoid materialising 250k-line chunks on the 8 GB grader box.
        with self.client.stream("GET", f"/events/chunks/{chunk_id}") as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.strip():
                    yield line

    def get_workflows(self) -> list[dict[str, Any]]:
        resp = self.client.get("/workflows")
        resp.raise_for_status()
        return resp.json()

    def get_workflow(self, wid: str) -> dict[str, Any]:
        resp = self.client.get(f"/workflows/{wid}")
        resp.raise_for_status()
        return resp.json()

    def get_config(self, wid: str) -> dict[str, Any]:
        resp = self.client.get(f"/workflows/{wid}/config")
        resp.raise_for_status()
        return resp.json()

    def get_config_history(self, wid: str) -> list[dict[str, Any]]:
        resp = self.client.get(f"/workflows/{wid}/config/history")
        resp.raise_for_status()
        return resp.json()

    def auto_apply(self, wid: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        resp = self.client.put(f"/workflows/{wid}/config", json=payload)
        return resp.status_code, resp.json()

    def request_approval(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        resp = self.client.post("/approvals", json=payload)
        return resp.status_code, resp.json()

    def list_approvals(self, workflow_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        params = {}
        if workflow_id:
            params["workflow_id"] = workflow_id
        if status:
            params["status"] = status
        resp = self.client.get("/approvals", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_approval(self, aid: str) -> dict[str, Any]:
        resp = self.client.get(f"/approvals/{aid}")
        resp.raise_for_status()
        return resp.json()

    def apply_approval(self, wid: str, approval_id: str) -> tuple[int, dict[str, Any]]:
        resp = self.client.post(f"/workflows/{wid}/apply", json={"approval_id": approval_id})
        return resp.status_code, resp.json()

    def suggest_fixes(self, wid: str, problem: str) -> list[dict[str, Any]]:
        resp = self.client.post("/llm/suggest-fixes", json={"workflow_id": wid, "problem": problem})
        resp.raise_for_status()
        return resp.json()

    def run_validation(self, wid: str, prompt_version: str, n: int = 100) -> dict[str, Any]:
        resp = self.client.post("/validation/runs", json={"workflow_id": wid, "prompt_version": prompt_version, "n": n})
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()
