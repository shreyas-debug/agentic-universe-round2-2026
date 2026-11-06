# Outcome-First Agent Loop — Submission

**Name:** Shreyas Satpute · **Date:** 6 November 2026 · **Hours actually spent:** Not reliably recorded across the pre-existing local work and this completion pass · **Language / storage:** Python 3.13 / SQLite

## 0. TL;DR

The controller ingests simulator events over HTTP, tests welcome/voice allocations for engagement-primary workflows, detects degraded caller IDs and hallucination populations, and routes changes through governance. Its SQLite store keeps the event cursor, decision state, proposals and audit records. The public_02 run corrected the flagged caller ID and bad supervisor prompt without a harmful applied change. I cut per-person scheduling, a supervisor UI and automatic decay rollback to keep the safety and recovery paths reviewable.

## 1. Assumptions

1. The simulator's documented HTTP API is the only runtime source of facts. The controller never imports `sim`, reads `runs/`, or parses a scenario.
2. A one-hour control tick is acceptable in this assignment. Detection can lag the underlying event by up to one simulated hour, plus approval time.
3. A workflow's `daily_batch.size` is a rough affected-lead estimate for a proposed change, not an exact count of in-flight leads.
4. Synthetic validation measures prompt safety only. It does not prove engagement lift.
5. Caller-ID health is compared within a workflow, using a recent sample. A low-volume or single-DID workflow has insufficient evidence for rotation.
6. For this prototype, one SQLite writer owns the controller state. Production would use a transactional shared store and workflow ownership leases.
7. A controller process restart retains its SQLite file and the simulator process remains alive, as the contract states.

## 2. Scope decisions

| # | Capability | Status | Reason / what remains |
|---|---|---|---|
| C1 | Restartable event ingestion | Built | Chunk cursor and aggregate state commit together; add partitioned ingestion for production. |
| C2 | Shared cohorts and feature joins | Partial | Config cohorts and event cohort IDs are shared; individual lead-feature joins and supervisor view are cut. |
| C3 | History bootstrap | Partial | Last available two simulated days seed variant estimates; a chunk straddling the cutoff is included whole. |
| C4 | Call-time optimisation | Cut | Avoided schedule churn and unmeasured capacity effects; needs randomized hour experiments. |
| C5 | Caller-ID health | Built | Recent within-workflow population comparison and governed rotation; refine with hierarchical seasonal baseline. |
| C6 | Welcome/voice experiment | Partial | Uniform exploration and deterministic proportion test; no joint interaction model. |
| C7 | Decay and rollback | Partial | A prior-winner heuristic exists, but cumulative estimates can miss decay and there is no reliable automatic rollback; needs rolling, version-aware control groups. |
| C8 | Hallucination response | Partial | Population trigger, LLM suggestion, validation, approval, post-apply observation; no automated engagement canary. |
| C9 | Governance lifecycle | Built | Durable intent, approval reconciliation, policy checks, stale and lapse handling. Re-proposal is deferred to the next decision window. |
| C10 | Impact and dry run | Partial | Honest estimate and explicit uncertainty; no causal replay from historical cohorts. |
| C11 | Supervisor view | Cut | Audit CLI is available; a unified product view needs design and user testing. |
| C12 | Audit store | Built | Queryable action/proposal chain in SQLite; per-call trace is not retained. |
| C13 | Crash recovery | Built | Kill/restart harness and evidence in `reports/crash_recovery.json`. |
| C14 | Loop observability | Partial | Per-tick event count, ingestion time, tick time and credits in SQLite; production alerts are specified below. |
| C15 | Fair attention allocation | Cut | Workflow-local limits exist; tenant-wide fairness scheduler remains. |

I prioritized changes whose failure would violate a hard constraint: event loss, unauthorized config edits, unexplainable decisions, and unsafe incident response. The next priority was measurable lift without constant configuration churn.

## 3. Stakeholders

### 01 — Sales

Accepted: speed matters after evidence is sufficient; the loop polls approvals every tick. Declined: immediate rollout of a lunchtime winner and cross-client promotion. Repeated looks, client policy and prompt safety make that unsafe. The memo's claims are wrong: Kestrel's auto-apply is limited to listed dimensions and a 0.2 maximum shift; Northwind KYC has auto-apply disabled.

**Reply:** We can move quickly inside each workflow's policy. Kestrel permits limited automatic allocation and caller-ID changes; Northwind KYC still requires approval. I will not copy a persuasive prompt from another account without workflow-specific validation and approval. The controller sends a small number of evidence-backed requests, rather than several daily guesses.

### 02 — Northwind Compliance

Accepted: customer-facing changes require approval; prompt proposals include validation evidence. Schedule and caller-ID changes use auto-apply only when the workflow policy allows. Declined: a promise of customer-level audit within 24 hours from this prototype; it retains config decisions and variant summaries, not every call record. The policy does not provide a blanket approval for all operational changes: Northwind KYC has auto-apply disabled.

**Reply:** Each controller change is tied to a proposal ID, base config version, rationale and approval or policy-permitted auto-apply. Prompt changes include a validation run ID. I can explain why a workflow version changed. A customer-level answer requires retaining call-to-version evidence in a protected store; I have marked that as a production prerequisite, not claimed it exists today.

### 03 — Platform/SRE

Accepted: one SQLite state store, transactional chunk checkpoints, restart reconciliation and a measured load run. Declined: using Redis as a source of truth; it would complicate the exact cursor/state commit. The simulator's clock waits for the controller, so wall-clock load results do not prove live production freshness.

**Reply:** The kill/restart run finishes from the same SQLite file. Every submitted proposal has a durable intent and the controller reconciles approval state on restart. The load numbers, resource use and expected 10× limit are in section 8. I would page on a growing event backlog, inability to apply a time-limited approval, or exhausted incident credits; routine proposal rejection can wait for business hours.

### 04 — Kestrel Operations

Accepted: treat supervisor edits as authoritative, avoid early requests, cap requests at two per 24 hours for Kestrel, and route customer-facing changes through approval unless policy permits a small shift. Declined: a one-screen UI in this pass. The signed policy actually allows up to four requests per 24 hours and permits limited auto-apply; the controller voluntarily uses a stricter two-request limit.

**Reply:** I will not silently restore an older prompt after your edit. A suspected bad prompt becomes a request with the observed QA population and validation evidence. Requests start after 11:00 and are limited to two per day for Kestrel. The audit command shows what changed. A consolidated supervisor screen is the next product surface, and the current prototype does not claim to provide it.

### 05 — Product

Accepted: separate pickup and engagement, shared config cohort IDs, explicit proposal fields and deterministic winner tests. Declined: per-person call time and supervisor UI in the first pass. An expired proposal is not the same lifecycle state as a rejection: it needs fresh evidence and a new ID before a later request. An arm that was never applied cannot contribute observed calls.

**Reply:** The controller measures pickup per dial and engagement per connected call. It tests only variants that generated call events and leaves KYC welcome/voice allocations unchanged because pickup is its primary KPI. Proposals carry a diff, estimated leads, a bounded impact score and a dry-run field that states its limits. I have kept expiry distinct from rejection in the store so later re-proposals can be traced correctly.

### 06 — CTO

Accepted: explainability, bounded credit use, an explicit scope cut and an operational handoff. Declined: broad feature coverage with shallow failure handling. The memos' conflicting claims are resolved against each workflow's actual governance block, not by role seniority.

**Reply:** This submission concentrates on a smaller governed loop that can finish the public scenarios, explain its changes and survive a restart. It is not a production-ready decision platform: decay rollback, per-call audit, causal impact estimates and a tenant fairness scheduler are missing. The reports show both where the loop helped and where it did not. Those gaps are the first engineering plan, not hidden behind a claimed lift figure.

## 4. Architecture

```text
Simulator HTTP chunks ──> ingestion ──> cohort/variant counters ──> decision loop
                               │                   │                    │
                               └── SQLite cursor + runtime snapshot     ├── caller-ID detector
                                                   │                    ├── experiment test
                                                   │                    └── QA incident detector
                                                   │                              │
                                                   └──────── governance intent ──┘
                                                              │
                                                     /approvals or permitted PUT
                                                              │
                                                     reconcile + audit SQLite
```

The cursor and derived counters are written in one SQLite transaction after each chunk. Before every advance, the loop drains unseen event chunks, covering a crash after `/sim/advance` but before ingestion. Approval submissions are stored as `sending` before HTTP; restart reconciliation finds them by proposal ID. The process owns one SQLite writer. It does not need a broker for the simulated world.

## 5. The learning loop

**Cohorts.** `senior`, `young` and tier-one definitions are generated once per workflow and sent as config cohorts. The simulator's event `cohort_id` is the measured identity. The current cut does not retain a separate lead-feature join; cohort traffic below 200 connected calls per arm is not promoted.

**Experiment design.** When engagement is primary, untried welcome and voice options receive uniform traffic. The simulator randomizes these dimensions independently, so this is a marginal estimate, not an interaction estimate. A pickup-primary workflow does not test these post-pickup dimensions. No arm is credited until actual call events arrive.

**Winner test.** A deterministic two-proportion z-test compares the leading observed arm with the runner-up after at least 200 connected calls per arm. The promotion cutoff is z ≥ 3.3, deliberately stricter than an unadjusted 1.96 because multiple arms and daily looks are involved. This is a conservative heuristic, not a formal family-wise error guarantee. The guardrail's observed rate is checked separately. Allocation remains 80% winner and 20% exploration.

**Decay/rollback.** A prior-winner peak is retained and a large observed drop can pivot to another arm, but cumulative counters obscure gradual decay and there is no reliable rollback to a safe control. The public reports show continuing exposure to W3. A production fix needs rolling, config-version-specific windows and a control allocation before automated rollback.

**Call time.** Cut; no schedule weights or per-person preferred hours are changed. **Caller ID.** A DID needs at least 150 recent dials and a pickup rate less than 55% of the best sufficiently sampled peer before rotation. This is a conservative population trigger, not proof of carrier spam status.

**Guardrail.** Pickup and engagement are computed with their own denominators. A proposed arm is rejected if the observed guardrail drop exceeds the workflow maximum. Sampling uncertainty remains a known weakness; section 13 describes the stronger test needed.

## 6. Governance

`sending → pending → approved → applied`, with `rejected`, `expired`, `lapsed`, `stale` and `auto_applied` terminal paths. The controller reads workflow policy at runtime and checks changed dimensions and total-variation shift before auto-apply. It reconciles simulator approvals and config history on restart. A supervisor edit is recorded; an old approved proposal cannot overwrite its newer base version. A fresh proposal may be considered at the next decision window.

Requests are limited by the workflow's 24-hour policy; Kestrel is voluntarily capped at two and starts at 11:00. The loop submits experiment proposals once daily at 11:00. Incident and DID proposals can happen on other ticks during allowed hours. Expiry and apply windows are simulator-controlled; approved requests are applied on the next hourly reconciliation. This can lapse if an apply window is shorter than the tick.

`impact_score` ranks estimated urgency or observed uplift; it is not a causal probability. `affected_leads` uses the published daily batch size as an estimate. The dry-run payload explicitly states that it is an observational comparison and does not infer counterfactual outcomes. No prompt change is auto-applied.

## 7. Incidents

A hallucination investigation needs at least 200 connected calls, at least 10 flagged calls and a 4% recent flag rate in a 300-call window. The detector reserves 12 credits before calling the LLM. At most two prompt candidates are tested at 400 synthetic conversations each. The candidate must exceed both 98.5% and the workflow's validation threshold. Following a supervisor prompt edit, a previously used production prompt is tested first. Validation supports safety only; the proposal says engagement impact is unknown.

In public_02 the caller-ID incident starts at D2 10:00. The grader reports zero share on the flagged DID from D3. A supervisor prompt edit occurs at D3 11:30; the controller submits a validated rollback at D3 14:00 and applies the approved change at D3 15:00. The grader reports 210 simulated minutes to fix. A real rollback decision from post-apply engagement is not yet automated.

## 8. Scale

On public_03, 4,200,115 live events were ingested across 12 workflows and five simulated days. HTTP NDJSON ingestion averaged 77,273 events/s during ingestion; the whole control loop averaged 20,964 events/s and 40.1 wall seconds per simulated day. p95 tick duration was 3.96 seconds; observed peak controller working set was 44.7 MB. The hourly decision cadence gives up to 60 simulated minutes of event age before computation. The full method and caveats are in `reports/public_03.md`. At 10×, one-process HTTP JSON parsing and serial SQLite checkpointing become the first bottlenecks. Production partitioning would assign workflows to leased workers and store offsets plus aggregates transactionally. The simulator waits for the loop; a real dialler needs an independent backlog alert.

## 9. Reliability

`python scripts/crash_recovery.py` kills the controller at simulated minute 660, restarts it against the still-running simulator, and checks completion and uniqueness of applied proposal IDs. The saved result is in `reports/crash_recovery.json`. Event cursor and aggregates are committed together; uncommitted work on a chunk is replayed. Proposal IDs are UUIDs and persisted before the HTTP request. After a process death, a `sending` proposal is matched against `/approvals`; an `auto_sending` change is matched against config history before retry.

HTTP 4xx policy errors are recorded as failed proposals; stale approvals are never forced through. HTTP 5xx or network failure interrupts the current loop, leaving durable state for restart. This is safer than advancing and silently discarding an event chunk. It does require a supervisor or process manager to restart a failed process.

## 10. Audit & observability

Example: `python main.py audit --workflow ks_cancel --at 5200 --db runs/final02_controller.db` queries the controller's own `audit_log` for the action, proposal ID, approval ID, rationale and config version. It shows the D3 11:30 supervisor edit, D3 14:00 prompt rollback proposal (`P2 → P1`, 15/300 QA flags, validation run `val_00001`), approval `apr_00006`, and D3 15:00 apply as version 5. The store also holds per-tick `loop_metrics`: events ingested, ingestion duration, total tick duration and remaining credits.

Page on an event backlog that grows for two ticks, a pending approved proposal near `apply_by`, an incident with fewer than 12 credits, or repeated controller crashes. Send a business-hours alert for one rejected proposal, ordinary experiment uncertainty or a single noisy QA flag.

## 11. Results

The final public reports are `reports/public_01.md` and `reports/public_02.md`. Their summary tables are below; deltas are versus the no-op baseline.

| Scenario | Workflow | Conclusion | Δ | Pickup | Δ | Engagement | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| public_01 | ks_cancel | 0.341 | +2.4% | 0.243 | +2.0% | 0.491 | +0.1% |
| public_01 | nw_kyc | 0.359 | -1.1% | 0.238 | -1.7% | 0.588 | -0.5% |
| public_01 | nw_renewal | 0.433 | +3.2% | 0.265 | +0.5% | 0.561 | +2.9% |
| **public_01** | **ALL** | **0.376** | **+2.0%** | **0.250** | **+0.8%** | **0.535** | **+1.0%** |
| public_02 | ks_cancel | 0.370 | +49.4% | 0.246 | +33.8% | 0.512 | +7.6% |
| public_02 | nw_kyc | 0.372 | -0.8% | 0.245 | +1.1% | 0.586 | -1.0% |
| public_02 | nw_renewal | 0.419 | -1.8% | 0.260 | -0.5% | 0.542 | -1.0% |
| **public_02** | **ALL** | **0.386** | **+15.7%** | **0.251** | **+12.9%** | **0.537** | **+1.4%** |

Public_01 applied two changes: one beneficial and one neutral by the grader's hidden-truth classification. Public_02 applied six, all classified beneficial, with no harmful applied change. Two public_02 requests were rejected by approver judgement and were not applied. The Kestrel incident drives most of the aggregate improvement. Northwind renewal and KYC remain below their no-op baselines on some measured KPIs; those are real weaknesses even though their applied changes were classified beneficial at application time. The controller has no automatic decay rollback. The grader's hidden-truth labels are used only for this post-run analysis, never by the controller.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Decision |
|---|---|---|
| Per-person preferred hour | Potential pickup lift | Cut: observational time preference is confounded by who was available and dialled. |
| Frequent schedule rewrites | Fast adaptation | Cut: previous local report had dozens of neutral changes; avoid audit noise and capacity risk. |
| LLM-chosen winner | Flexible variant ranking | Rejected: winner selection must be statistical and deterministic. |
| Highest validation pass prompt | Simplest incident selection | Rejected: safety-only validation can favor a rigid prompt that hurts engagement. |
| Redis state | Fast counters | Rejected: cursor and counters need one transaction in this single-process prototype. |

## 13. Known weaknesses

- The one-hour tick can miss a short apply window or delay incident response.
- Multiple comparisons and repeated looks are addressed by a stricter z cutoff, not a formal sequential test.
- Marginal welcome/voice comparisons do not estimate interactions or fully adjust for time/cohort imbalance.
- No reliable automatic decay rollback; W3 exposure persists in the public reports.
- No exact customer-level audit trail, supervisor UI or tenant-wide credit fairness.
- A crash during LLM suggestion/validation may repeat that credit spend on restart.
- SQLite is local to one process and the checkpoint serializes aggregate state each chunk.

## 14. If this went to production at Formi next month

1. Week 1: add protected call-to-config-version audit retention, replayable event offsets and a workflow lease in the existing Postgres deployment.
2. Week 1: run shadow mode on live traffic, measure detection false positives, lag, DID seasonality and approver workload.
3. Week 2: replace the heuristic winner test with a prespecified sequential or fixed-horizon design with guardrail confidence bounds and joint welcome×voice analysis.
4. Week 2: add small guarded canaries, rolling control groups and automatic rollback for prompt engagement and decaying variants.
5. Week 2: expose a single supervisor view and wire paging for backlog, approval deadlines and incident credit reserve.
