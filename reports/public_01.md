# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.343 | +2.9% | 0.244 | +2.4% | 0.492 | +0.2% | -1% | 67 (47) |
| nw_kyc | 0.374 | +2.8% | 0.245 | +1.1% | 0.596 | +1.0% | 0% | 18 (21) |
| nw_renewal | 0.423 | +0.7% | 0.260 | -1.2% | 0.557 | +2.1% | 19% | 38 (42) |
| ALL | 0.376 | +2.0% | 0.250 | +0.8% | 0.535 | +1.0% | 7% | 123 (110) |

## Changes applied by the controller

2 applied — beneficial: 1, neutral: 1

- nw_renewal v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=+0.067 didΔ=+0.000 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 2,
  "decisions": {
    "approved": 2,
    "expired": 0
  },
  "rejection_reasons": {},
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
    "3": 0.324,
    "4": 0.338,
    "5": 0.33,
    "6": 0.334,
    "7": 0.336
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0