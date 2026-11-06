"""Produce load numbers from controller SQLite metrics and simulator HTTP metadata.

This reporting script is outside the controller. It never reads scenario truth.
Usage: python scripts/load_report.py URL DB --peak-mb 45 --out reports/public_03.md
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3

import httpx


def percentile(values, pct):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * pct) - 1)] if values else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("db")
    parser.add_argument("--peak-mb", type=float, required=True,
                        help="observed peak working set of controller process")
    parser.add_argument("--out", default="reports/public_03.md")
    args = parser.parse_args()

    with sqlite3.connect(args.db) as db:
        rows = db.execute(
            "SELECT sim_minute,events_ingested,ingest_seconds,tick_seconds,credits_remaining "
            "FROM loop_metrics ORDER BY sim_minute"
        ).fetchall()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        state = client.get("/sim/state").json()
        chunks = client.get("/events/chunks", params={"kind": "events"}).json()
    if not state["finished"] or not rows:
        raise SystemExit("load run is not complete")
    live_events = sum(chunk["events"] for chunk in chunks)
    ingested = sum(row[1] for row in rows)
    if live_events != ingested:
        raise SystemExit(f"ingestion mismatch: API={live_events} controller={ingested}")
    total_tick = sum(row[3] for row in rows)
    total_ingest = sum(row[2] for row in rows)
    days = state["end_minute"] / 1440
    report = f"""# Public 03 load report

Measured on Windows, Python 3.13, local FastAPI simulator and SQLite controller. The evaluation target is 4 vCPU / 8 GB Linux; this is a local measurement, not a claim of identical hardware.

| Measure | Result |
|---|---:|
| Simulated days / workflows | {days:g} / 12 |
| Live events ingested (HTTP chunk metadata agrees with SQLite) | {ingested:,} |
| HTTP NDJSON ingestion throughput (events / ingestion wall time) | {ingested / total_ingest:,.0f}/s |
| End-to-end loop throughput (events / tick wall time) | {ingested / total_tick:,.0f}/s |
| Mean wall time per simulated day | {total_tick / days:.1f} s |
| p95 / max wall time per one-hour tick | {percentile([r[3] for r in rows], .95):.2f} / {max(r[3] for r in rows):.2f} s |
| p95 / max ingestion time per tick | {percentile([r[2] for r in rows], .95):.2f} / {max(r[2] for r in rows):.2f} s |
| Decision cadence / worst freshness before computation | 60 simulated min |
| Controller peak working set observed via OS | {args.peak_mb:.1f} MB |
| Credits remaining | {rows[-1][4]:.0f} |

The controller processes all available event chunks before the next advance. A new event can wait up to one simulated hour for the next decision, then one tick's computation and any governance delay. This is a cadence bound, not a measured percentile of individual decision times. Simulator history generation and the two-day bootstrap are excluded from the live throughput denominator. The controller bootstraps a bounded amount of history to avoid retaining every historical lead.

At 10×, one process doing HTTP NDJSON parsing and serial SQLite checkpoint serialization becomes the first throughput limit. Partition by workflow with a lease, transactional offset plus aggregate update, and a bounded worker queue. A real-time dialler would also require an alert on event age because its clock does not wait for decisions.
"""
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
