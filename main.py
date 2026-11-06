"""Entry point for the Outcome-First Agent Loop Controller.

Usage:
    python main.py run --url http://127.0.0.1:8700
    python main.py audit --workflow nw_renewal --at 4320
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from controller.loop import OutcomeController
from controller.storage import Storage


def main():
    parser = argparse.ArgumentParser(description="Outcome-First Agent Loop Controller")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the controller loop against the simulator")
    run_parser.add_argument("--url", default="http://127.0.0.1:8700", help="Simulator base URL")
    run_parser.add_argument("--step", type=int, default=60, help="Advance step in minutes (default: 60)")
    run_parser.add_argument("--db", default=os.environ.get("CONTROLLER_DB", "controller.db"), help="SQLite database path")

    # Audit command
    audit_parser = subparsers.add_parser("audit", help="Query the decision/audit store")
    audit_parser.add_argument("--workflow", required=True, help="Workflow ID to query")
    audit_parser.add_argument("--at", type=int, default=None, help="Simulated minute to query at/before")
    audit_parser.add_argument("--db", default="controller.db", help="SQLite database path")

    # Allow direct invocation like `python main.py http://127.0.0.1:8700`
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-") and sys.argv[1] not in ("run", "audit"):
        sim_url = sys.argv[1]
        controller = OutcomeController(sim_url=sim_url, db_path=os.environ.get("CONTROLLER_DB", "controller.db"))
        controller.run(step_minutes=60)
        return

    args = parser.parse_args()

    if args.command == "audit":
        storage = Storage(db_path=args.db)
        records = storage.query_audit(workflow_id=args.workflow, at_minute=args.at)
        print(f"\n=== Audit Trail for Workflow '{args.workflow}' (count: {len(records)}) ===")
        for r in records:
            print(f"[{r['ts']} | Sim Min {r['sim_minute']}] Action: {r['action']} | Actor: {r['actor']}")
            if r["from_version"] or r["to_version"]:
                before = f"v{r['from_version']}" if r["from_version"] else "unknown"
                after = f"v{r['to_version']}" if r["to_version"] else "pending"
                print(f"  Version: {before} -> {after}")
            if r["proposal_id"]:
                print(f"  Proposal ID: {r['proposal_id']} | Approval ID: {r['approval_id']}")
            if r["diff"]:
                print(f"  Diff: {r['diff']}")
            if r["rationale"]:
                print(f"  Rationale: {r['rationale']}")
            if r["evidence"]:
                print(f"  Evidence: {r['evidence']}")
            print("-" * 60)
    else:
        url = getattr(args, "url", "http://127.0.0.1:8700")
        step = getattr(args, "step", 60)
        db = getattr(args, "db", "controller.db")
        controller = OutcomeController(sim_url=url, db_path=db)
        controller.run(step_minutes=step)


if __name__ == "__main__":
    main()
