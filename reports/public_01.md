# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.341 | +2.4% | 0.243 | +2.0% | 0.491 | +0.1% | -2% | 46 (47) |
| nw_kyc | 0.359 | -1.1% | 0.238 | -1.7% | 0.588 | -0.5% | 0% | 22 (21) |
| nw_renewal | 0.433 | +3.2% | 0.265 | +0.5% | 0.561 | +2.9% | 18% | 41 (42) |
| ALL | 0.376 | +2.0% | 0.250 | +0.8% | 0.535 | +1.0% | 6% | 109 (110) |

## Changes applied by the controller

2 applied — neutral: 1, beneficial: 1

- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.068 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 3,
  "decisions": {
    "approved": 2,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 2,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "4": 0.341,
    "5": 0.332,
    "6": 0.341,
    "7": 0.337
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0