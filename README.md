# SagePulse — SageMaker cost guardrails

A scheduled Lambda that finds idle SageMaker notebooks and endpoints, estimates what they cost, and applies a small tag-based policy: stop what is clearly disposable, escalate what is expensive in production, never touch what is critical. Terraform, Python 3.12, 31 tests, GitHub Actions with OIDC.

Every number in this README can be checked in the repository.

---

## The problem

SageMaker notebooks are billed while they are *running*, not while someone is using them. A data scientist opens an `ml.t3.xlarge` on Monday, forgets it, and it bills 730 hours a month at roughly $0.20/hour — about $146 for nothing. Inference endpoints are worse: an `ml.m5.xlarge` endpoint that nobody calls still runs 24/7 at about $180/month, and a forgotten `ml.p3.2xlarge` at about $3,000.

Nobody sees this on the bill because SageMaker shows up as one line. And a generic "stop everything idle" script is dangerous: it will stop the production endpoint or the notebook holding a regulated dataset.

## The goal

1. Every four hours, list in-service notebooks and endpoints.
2. Estimate each one's monthly cost from its instance type (endpoints: per variant × instance count).
3. Check CloudWatch for activity: notebook CPU over 24 h, endpoint invocations over 24 h.
4. Decide with tags, not guesses:

| Tags on the resource | Idle? | Est. cost | Decision |
|---|---|---|---|
| `DataCriticality=high` | — | — | notify, never touch |
| `Environment=prod` | — | > `HIGH_THRESHOLD` (50 $/mo) | escalation email |
| `Environment=prod` | — | ≤ threshold | silent |
| `AutoStop=true` (notebook) | yes | > `IDLE_COST_THRESHOLD` (10 $/mo) | **stop** + email |
| `AutoStop=true` (endpoint) | yes | > threshold | email "idle endpoint" — endpoints are never deleted automatically |
| anything else | — | — | log line only, no email |

5. Ship with `DRY_RUN=true`: every decision is logged and emailed with a `[DRY_RUN]` prefix, nothing is stopped, until an operator flips the variable.

Auto-stop is **opt-in** (`AutoStop=true`) and restricted to notebooks. The default behaviour of the platform on a resource it knows nothing about is to do nothing and say nothing. That is deliberate: the previous version emailed "monitoring only" for every resource every four hours, and people stopped reading.

## What is implemented

| Component | Where | Notes |
|---|---|---|
| Guardrail Lambda | `lambda/guardrail.py` (≈300 lines) | boto3 + Lambda Powertools logger |
| Cost estimate | `estimate_monthly_cost()` | 15 instance types priced (eu-west-1 on-demand, rounded); unknown types use a $0.10/h fallback and are flagged in the email |
| Endpoint sizing | `get_endpoint_instances()` | reads `DescribeEndpointConfig` variants; serverless variants cost 0 |
| Idle detection | `is_idle()` | notebook CPU from `/aws/sagemaker/NotebookInstances`, endpoint `Invocations` with `VariantName=AllTraffic`; any CloudWatch error → "not idle" (fail safe) |
| Schedule | `terraform/eventbridge.tf` | `rate(4 hours)` |
| Budget | `terraform/budgets.tf` | AWS Budgets on the SageMaker service: 80 % actual and 100 % forecast → SNS |
| Notifications | SNS email | one topic, shared by the Lambda and the budget |
| IAM | `terraform/iam.tf` | one role for the Lambda: list/describe/tags on SageMaker, `StopNotebookInstance`, `GetMetricStatistics`, `sns:Publish` on the one topic |
| CI/CD | `.github/workflows/ci.yml` | ruff → pytest (coverage gate 85 %, currently 94 %) → terraform fmt/validate → Checkov (soft fail) → build zip → on `main`: `update-function-code` via OIDC |
| Deploy role | `terraform/oidc.tf` | GitHub OIDC, scoped to `lambda:UpdateFunctionCode` on the one function, `sub` pinned to this repo's `main` |

Not implemented: rightsizing recommendations, Studio apps / training jobs, Slack, multi-account, real billing data (the estimate is a price table, not Cost Explorer). See roadmap.

## Architecture

```
AWS Budgets (SageMaker, monthly) ──► SNS ◄────────────────────────────────┐
                                                                            │
EventBridge rate(4h) ──► Lambda guardrail                                   │
                            ├─ ListNotebookInstances / ListEndpoints         │
                            ├─ DescribeEndpointConfig (variants × count)     │
                            ├─ ListTags                                      │
                            ├─ CloudWatch GetMetricStatistics (24 h)         │
                            ├─ policy → stop notebook (if !DRY_RUN) ─────────┤
                            └─ policy → notify ──────────────────────────────┘
```

## Run it

```bash
# tests
pip install -r requirements.txt -r requirements-dev.txt -r lambda/requirements.txt
AWS_REGION=eu-west-1 pytest tests/ --cov=lambda

# one-time bootstrap of remote state (bucket and lock table names are in terraform/main.tf)
aws s3 mb s3://ml-cost-optimizer-tfstate --region eu-west-1
aws dynamodb create-table --table-name ml-cost-optimizer-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST --region eu-west-1

# build + deploy (dry-run on)
cd lambda && pip install -r requirements.txt -t package/ && cp guardrail.py package/ \
  && (cd package && zip -qr ../function.zip .) && cd ..
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # set notification_email
cd terraform && terraform init && terraform apply
```

`setup.sh` does the same steps interactively. After a few `[DRY_RUN]` emails look right, set `dry_run = false` and re-apply. The output `github_deploy_role_arn` goes into the `AWS_DEPLOY_ROLE_ARN` GitHub secret for the CI deploy job.

Invoking the Lambda by hand returns a decision summary, e.g. `{"statusCode": 200, "dry_run": true, "decisions": {"monitor": 6, "stop_skipped": 2, "escalate_prod": 1}}`.

## Known limits

- The cost figure is an on-demand price table × 730 h, not the bill. Savings Plans, spot and regional price differences are not reflected. The email says when a fallback price was used.
- A notebook with no CloudWatch datapoints in 24 h is treated as idle. That is correct for a running-but-unused instance, and also what you get for the first hour after a start; the cost threshold and the `AutoStop` opt-in keep that from mattering.
- Only notebook instances and real-time endpoints are covered. Studio apps, training jobs and batch transform are not scanned.
- Single account, single region.

## Project history

v1 (see `CHANGELOG.md`, kept as an engineering journal) was three Lambdas orchestrated by Step Functions with a human-approval step, Cost Explorer queries, S3 reports and a DynamoDB deduplication table. It produced a nice report and nobody acted on it. v2 replaced it with one Lambda and a policy expressed as tags, because the useful decisions turned out to fit in a five-row table, and the only action worth automating is stopping a dev notebook.

## Roadmap

- Cover SageMaker Studio apps (`ListApps`) and long-running training jobs.
- Replace the price table with the Pricing API, cached in the Lambda.
- Optional Slack webhook next to SNS.
- Per-team weekly digest instead of per-event emails.

## License

MIT
