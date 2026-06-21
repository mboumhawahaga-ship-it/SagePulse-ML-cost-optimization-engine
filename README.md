[![CI/CD](https://github.com/mboumhawahaga-ship-it/SagePulse/actions/workflows/ci.yml/badge.svg)](https://github.com/mboumhawahaga-ship-it/SagePulse/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-88%25-brightgreen)](https://github.com/mboumhawahaga-ship-it/SagePulse)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org)
[![Terraform](https://img.shields.io/badge/terraform-%3E%3D1.0-purple)](https://www.terraform.io)

# SagePulse — SageMaker Cost Guardrail Engine

**Automatically detect idle SageMaker resources, enforce tag-based stop policies, and alert before GPU waste turns into a surprise AWS bill.**

---

## Business Problem

SageMaker has no native mechanism to stop idle resources. Notebooks and endpoints run until someone explicitly stops them — or until the bill arrives.

The cost profile makes this worse than typical cloud waste:

- `ml.t3.medium` notebook: $0.05/h — low stakes, easy to forget
- `ml.m5.xlarge` notebook: $0.23/h — $168/month per idle instance
- `ml.p3.2xlarge` notebook: $3.83/h — **$2,795/month** if never stopped, ~$75 lost overnight

In a team with 5–10 data scientists, each running notebooks independently, idle GPU time accumulates fast. The default AWS tools (Budgets, Cost Explorer) tell you *after the fact* that spend exceeded expectations. They do not tell you *which resource* is idle right now, or stop it.

The gap: you need something that scans at the resource level, checks real utilization metrics, and acts — or alerts — before the next billing cycle.

---

## Use Cases

### Data Scientist — Weekend Notebook

A data scientist starts a `ml.p3.2xlarge` notebook on Friday for a training experiment. The job finishes Friday evening. The notebook stays `InService` through the weekend. At $3.83/h × 60h = **$230 wasted** before anyone notices Monday morning.

SagePulse scans every 4 hours. If the notebook's CPU stays below 5% for 24 consecutive hours and it has `AutoStop=true` + `Environment=dev` in its tags, the Lambda stops it automatically and sends an SNS notification. No human needed for dev resources.

### MLOps Team — Forgotten Inference Endpoint

A team deploys a SageMaker endpoint for a model demo during a quarterly sprint review. The sprint ends, the demo is done, but the endpoint keeps running. At $0.23/h for a `ml.m5.xlarge` endpoint × 720h = **$166/month** for a resource serving zero requests.

SagePulse detects zero `Invocations` over 24h via CloudWatch, flags the endpoint as idle, and sends an alert. Because endpoints are stateful (deleting one is irreversible), SagePulse requires human approval before acting — the notification includes the resource name, idle duration, and estimated monthly cost.

### Platform/MLOps Lead — Cost Guardrails Before Scaling

A small team is about to give 8 engineers access to SageMaker for a new AI project. The lead wants guardrails in place before spend spirals. SagePulse deploys in under 10 minutes via Terraform: an AWS Budget alert fires at 80% of the monthly SageMaker limit, the guardrail Lambda scans every 4 hours and stops idle dev notebooks automatically, and prod resources are protected by tag policy — no auto-action without `Environment=prod` being absent from the resource.

---

## System Overview

```
EventBridge (every 4h)
        │
        ▼
  Guardrail Lambda
        │
        ├── SageMaker API ──► list notebooks, endpoints
        │
        ├── CloudWatch ──► CPUUtilization (notebooks), Invocations (endpoints)
        │
        ├── AWS Pricing API ──► real hourly price per instance type
        │
        └── Tag evaluation ──► DataCriticality / Environment / AutoStop
                │
                ├── dev + idle + AutoStop=true ──► stop_notebook() [immediate]
                ├── prod + cost > threshold ──► SNS alert [human decision]
                ├── DataCriticality=high ──► SNS alert [no action]
                └── default ──► monitoring only

  Action Lambda (human-triggered)
        │
        └── approved: true ──► stop_notebook() / notify_idle_endpoint()

  AWS Budgets ──► SNS alert at 80% (actual) + 100% (forecasted)
```

The system separates *detection* (Guardrail Lambda, runs automatically) from *action* (Action Lambda, requires `approved: true` in the event payload). Notebooks in dev with `AutoStop=true` are stopped automatically. Endpoints are never deleted automatically — the cost of a false positive (deleting a real production endpoint) is too high.

---

## Optimization Logic

### Idle Detection

**Notebooks** — CloudWatch `CPUUtilization` metric, `AWS/SageMaker` namespace. A notebook is considered idle if its average CPU over the last 24 hours is below 5%. If no datapoints exist at all (notebook running but never used), it is treated as idle by default.

**Endpoints** — CloudWatch `Invocations` metric. An endpoint is considered idle if total invocations over the last 24 hours equal zero. A single invocation resets the idle clock.

24 hours was chosen as the window because it's long enough to avoid false positives from notebooks used sporadically (one training run per day) while being short enough to catch genuine overnight waste.

### Tag-Based Policy

The guardrail applies in priority order:

| Condition | Action |
|---|---|
| `DataCriticality=high` | Alert only — never touch, regardless of other tags |
| `Environment=prod` + cost > threshold | Escalation alert to SNS |
| `Environment=prod` | Monitoring only |
| `AutoStop=true` + idle + cost > $10 | Auto-stop (notebooks only) |
| Default | Monitoring alert |

This means a resource is protected by default. Auto-stop requires an explicit opt-in tag (`AutoStop=true`) combined with a non-prod environment. Production resources are never stopped automatically.

### Pricing

The Lambda calls the AWS Pricing API (`us-east-1` only — AWS constraint) to get the real on-demand hourly price for each instance type. If the API is unavailable or returns no result, it falls back to a hardcoded price map:

| Instance type | Hourly (fallback) | Monthly estimate |
|---|---|---|
| `ml.t3.medium` | $0.05 | $36.50 |
| `ml.t3.xlarge` | $0.20 | $146 |
| `ml.m5.xlarge` | $0.23 | $168 |
| `ml.p3.2xlarge` | $3.83 | $2,795 |

Monthly estimate = hourly × 730h (full month, worst case).

---

## What I Built

**`lambda/guardrail.py`** — Core Lambda, triggered by EventBridge every 4 hours. Scans all `InService` SageMaker notebooks and endpoints, evaluates idle status via CloudWatch, applies tag-based policy, auto-stops eligible notebooks, sends SNS alerts. Deployed: running on real AWS infrastructure.

**`lambda/package/discovery.py`** — Resource scanner. Lists notebooks (with pricing, idle check, carbon footprint estimate), Studio apps (JupyterServer, KernelGateway, JupyterLab), endpoints (with idle check), and completed training jobs. Builds the full inventory report. Used independently for discovery without action.

**`lambda/package/action.py`** — Human-approval gate. Accepts an event with `approved: true/false` and a list of idle resources. Stops notebooks on approval. Endpoints are flagged via SNS rather than deleted — endpoint deletion is irreversible, human decision required.

**`terraform/main.tf`** — Lambda deployment (Python 3.12, 256MB, 60s timeout), CloudWatch log group (7-day retention), SNS topic + email subscription.

**`terraform/eventbridge.tf`** — EventBridge rule at `rate(4 hours)` → Lambda invoke permission.

**`terraform/budgets.tf`** — AWS Budget scoped to `Amazon SageMaker` only (not total account). Two notifications: 80% actual spend and 100% forecasted. Forecasted alert fires before you hit the limit, not after.

**`terraform/iam.tf`** — Lambda role with least-privilege policy: `ListNotebookInstances`, `ListEndpoints`, `ListTags`, `StopNotebookInstance` on SageMaker; `GetMetricStatistics` on CloudWatch; `Publish` on the specific SNS topic ARN (not `sns:*`).

**`terraform/oidc.tf`** — GitHub Actions OIDC integration. CI/CD uses short-lived tokens, no long-lived AWS credentials stored in GitHub secrets.

**Tests** — 63 tests, 87% coverage (action 98%, discovery 86%, guardrail 85%). Uses `moto` for SageMaker/CloudWatch/SNS mocking. CI runs on every push via GitHub Actions.

---

## Assumptions and Data Sources

This is a working prototype deployed on a personal AWS account. The following applies:

**What runs on real AWS:**
- Guardrail Lambda executes on real SageMaker API calls
- CloudWatch idle detection uses real metric data
- Pricing API queries return real AWS prices
- Infrastructure deployed via Terraform with remote S3 state

**What uses estimates:**
- Carbon footprint (`calculate_carbon_footprint`) uses a static map based on instance family, not real power consumption data. Figures are indicative, not certified.
- Monthly cost estimates assume 730h/month (100% uptime). Real cost depends on actual run time.
- Idle detection threshold (CPU < 5%, invocations = 0 over 24h) is a reasonable heuristic, not a universal standard. A notebook running a long-lived training job at 4% CPU would incorrectly be flagged as idle.

**Scan frequency trade-off:**
Every 4 hours was chosen based on the v1 → v2 refactor feedback: weekly scans were too infrequent for GPU instances. A `ml.p3.2xlarge` idle overnight costs ~$75 before the next scan. 4h caps the maximum undetected waste at ~$15 per GPU instance per scan window.

---

## Results and Expected Impact

These are estimates based on common SageMaker usage patterns in small-to-medium ML teams (5–15 engineers), not measured production data.

| Scenario | Estimated monthly saving |
|---|---|
| 3 dev notebooks idle on weekends (ml.t3.xlarge) | ~$50–80/month |
| 1 forgotten inference endpoint (ml.m5.xlarge, 720h) | ~$165/month |
| 1 GPU notebook left on after training (ml.p3.2xlarge, 3 days) | ~$275/month |

Savings scale with team size, GPU usage, and tagging discipline. Auto-stop only applies to resources tagged `AutoStop=true` — teams that don't adopt the tagging convention get alerts but no automated action.

The biggest leverage point is GPU notebooks (`ml.p3`/`ml.g4dn`). CPU notebooks waste money slowly; GPU notebooks waste it fast. Targeting the `ml.p3.2xlarge` tier alone can justify the tool.

---

## Tech Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.12, AWS Lambda |
| Scheduling | Amazon EventBridge (`rate(4 hours)`) |
| Observability | CloudWatch Logs + Container Insights |
| Alerting | Amazon SNS (email) |
| Cost guardrail | AWS Budgets (SageMaker-scoped) |
| Infrastructure | Terraform >= 1.0, S3 remote state |
| CI/CD | GitHub Actions + OIDC (no stored credentials) |
| Testing | pytest, moto, 63 tests, 87% coverage |
| Linting | ruff, pre-commit |
| Observability in Lambda | aws-lambda-powertools 3.27.0 |

---

## Why I Built This

The v1 of this project ran a weekly scan and sent a report. It worked, but it was too slow for GPU workloads — by the time the weekly email arrived, the cost was already sunk.

The v2 refactor came from a concrete observation: SageMaker doesn't stop resources automatically. AWS gives you the infrastructure, not the discipline. If you're running a team of data scientists without any idle detection in place, you're relying on everyone remembering to stop their notebooks — which they won't, consistently.

Building this forced me to engage with three things that don't show up in typical cloud tutorials: the gap between "resource exists" and "resource is actually doing work" (CloudWatch metrics are the only reliable signal), the difference between detection and action (auto-deleting a production endpoint is a catastrophic mistake — human approval is not optional), and cost modeling at the resource level (AWS Budgets tells you the account total; getting per-resource cost requires hitting the Pricing API yourself).

The tag-based policy design (`DataCriticality`, `Environment`, `AutoStop`) reflects how these decisions actually work in teams: you need opt-in for automation, and you need a hard override for anything touching data or production.

Relevant for: MLOps Engineer, FinOps Engineer, Cloud Platform Engineer, Site Reliability Engineer roles.
