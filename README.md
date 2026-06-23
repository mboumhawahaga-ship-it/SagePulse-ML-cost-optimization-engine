# SagePulse - SageMaker Cost Guardrails Engine

## Overview

SagePulse is a serverless FinOps automation platform designed to continuously monitor Amazon SageMaker resources and enforce cost optimization guardrails.

The platform automatically detects idle notebooks and inference endpoints, evaluates resource criticality using business tags, and either stops non-critical workloads or escalates alerts for production systems.

Built with AWS serverless services and Infrastructure as Code, SagePulse helps organizations reduce unnecessary ML infrastructure spending while preserving production reliability.

## Business Problem

Machine learning environments often accumulate idle notebooks and underutilized endpoints that remain active for days or weeks.

Engineering teams frequently lack automated governance mechanisms to distinguish between critical production assets and disposable development environments, resulting in avoidable cloud spend.

SagePulse addresses this challenge by introducing automated FinOps guardrails for SageMaker workloads.

## Key Features

* Automated idle notebook detection using CloudWatch CPU metrics.
* Automated idle endpoint detection based on invocation activity.
* Tag-based business criticality classification.
* Automatic shutdown of non-critical idle resources.
* Escalation workflow for production workloads.
* Budget monitoring with actual and forecasted thresholds.
* Fully configurable cost and utilization thresholds.
* Secure CI/CD pipeline using GitHub OIDC authentication.
* Infrastructure provisioning with Terraform.

## Architecture

EventBridge triggers the guardrail engine every four hours.

The Lambda function:

1. Discovers active SageMaker notebooks and endpoints.
2. Retrieves CloudWatch utilization metrics.
3. Evaluates business tags and cost thresholds.
4. Applies automated remediation or escalation policies.
5. Publishes notifications through SNS.

```
AWS Budgets (monthly thresholds)
        │
        ▼
      SNS Topic ◄─────────────────────────────────┐
                                                   │
EventBridge (rate 4h)                              │
        │                                          │
        ▼                                          │
Lambda guardrail.py                                │
  ├── SageMaker API (notebooks + endpoints)        │
  ├── CloudWatch (CPU / Invocations)               │
  ├── DataCriticality=high  → notify only ─────────┤
  ├── Environment=prod      → escalate if cost > threshold
  ├── AutoStop=true + idle  → auto-stop + notify ──┤
  └── default               → monitoring only ─────┘
```

The entire platform operates using a fully serverless architecture.

## AWS Services

* AWS Lambda
* Amazon EventBridge
* Amazon SNS
* AWS Budgets
* Amazon SageMaker
* Amazon CloudWatch
* AWS IAM
* Amazon S3
* Amazon DynamoDB
* AWS STS (OIDC federation)

## Guardrail Logic

| Condition | Action |
|---|---|
| `DataCriticality=high` | Notify only — no action |
| `Environment=prod` + cost > threshold | Escalation alert |
| `Environment=prod` + cost ≤ threshold | No action |
| `AutoStop=true` + idle + cost > threshold | Auto-stop + notify |
| Default | Monitoring only |

## FinOps Capabilities

| Capability | Status |
|---|---|
| Idle resource detection | ✓ |
| Automated remediation | ✓ |
| Budget governance | ✓ |
| Forecast-based alerting | ✓ |
| Cost guardrails | ✓ |
| Rightsizing recommendations | Planned |
| Cost anomaly detection | Planned |

## Estimated Business Impact

The following figures are conservative estimates based on FinOps Foundation practices and common cloud optimization initiatives.

* Estimated 15-35% reduction in SageMaker development environment costs through automated idle resource shutdown.
* Estimated 10-25% reduction in endpoint waste by identifying unused inference endpoints.
* Estimated 20-50% faster detection of budget overruns through automated budget alerts and escalations.
* Platform operational cost estimated below $1/month for small and medium environments due to the serverless architecture.

### Example Savings Scenario

For an organization spending $10,000/month on SageMaker:

* Potential savings from idle resource management: $1,500–$3,500/month.
* Potential savings from unused endpoint detection: $1,000–$2,500/month.
* Total estimated optimization opportunity: $2,500–$6,000/month.

## Configuration

Thresholds are configurable via `terraform.tfvars` without modifying any code.

| Variable | Default | Description |
|---|---|---|
| `budget_limit_usd` | `100` | Monthly SageMaker budget limit |
| `high_threshold` | `50` | Cost threshold (USD/mo) for prod escalation |
| `idle_cost_threshold` | `10` | Minimum cost (USD/mo) to trigger auto-stop |

## DevOps & Security

* GitHub Actions CI/CD pipeline.
* Terraform Infrastructure as Code.
* OIDC federation with AWS (no long-lived credentials).
* Automated linting, testing and security scanning.
* Least-privilege IAM model.
* Secrets detection through pre-commit hooks.

### CI/CD Pipeline

```
lint (ruff) → test (pytest, coverage ≥ 80%) → build (function.zip)
                                                      │
                              security (Checkov IaC) ─┘
                                                      │
                                            deploy (main only)
                                       Terraform via OIDC
```

## Getting Started

```bash
# 1. Bootstrap remote state (one-time)
aws s3 mb s3://ml-cost-optimizer-tfstate --region eu-west-1
aws dynamodb create-table --table-name ml-cost-optimizer-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region eu-west-1

# 2. Build Lambda
cd lambda
pip install -r requirements.txt -t package/
cp guardrail.py package/
cd package && zip -r ../function.zip . && cd ../..

# 3. Deploy
cd terraform
terraform init
terraform apply
```

## Future Improvements

* Rightsizing recommendations.
* Cost anomaly detection.
* Slack and Microsoft Teams integrations.
* Historical cost analytics dashboard.
* Multi-account AWS Organizations support.

## License

MIT
