[![CI/CD](https://github.com/mboumhawahaga-ship-it/SagePulse/actions/workflows/ci.yml/badge.svg)](https://github.com/mboumhawahaga-ship-it/SagePulse/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-88%25-brightgreen)](https://github.com/mboumhawahaga-ship-it/SagePulse)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org)
[![Terraform](https://img.shields.io/badge/terraform-%3E%3D1.0-purple)](https://www.terraform.io)

# SagePulse — FinOps Optimization Engine for AWS SageMaker

---

## Business Context

SageMaker environments are often over-provisioned or forgotten after experimentation phases.

In ML teams, it is common for:

- notebook instances to remain idle for days
- inference endpoints to stay active without traffic
- training jobs to run longer than necessary
- development environments to be left running over weekends

This leads to significant and unnoticed cloud waste, often representing a large portion of ML infrastructure spend.

---

## 🎯 Business Objective

SagePulse is a FinOps automation engine for ML workloads on AWS, designed to:

- Detect idle and underutilized SageMaker resources
- Reduce unnecessary ML infrastructure spend
- Provide real-time cost visibility for MLOps teams
- Introduce controlled, human-approved remediation

---

## 💡 Key Business Outcomes

### 1. ML Cost Optimization

Continuously identifies unused SageMaker resources and highlights cost inefficiencies.

→ Reduces wasted spend in development and experimentation environments

### 2. Operational Visibility for MLOps Teams

Provides near real-time insight into:

- idle notebooks
- unused endpoints
- stuck or inefficient training jobs

→ Enables proactive cost management instead of monthly billing surprises

### 3. Controlled Cost Reduction (Human-in-the-loop)

No automatic destructive actions.

- alerts are generated automatically
- remediation requires explicit approval

→ Ensures safe FinOps automation without production risk

### 4. Reduction of Alert Noise

State tracking via DynamoDB ensures:

- no duplicate alerts per scan cycle
- stable and actionable notifications

---

## 📊 FinOps KPIs

| KPI | Description | Business Impact |
|-----|-------------|-----------------|
| **Idle Resource Cost** | Cost of unused SageMaker resources | direct savings |
| **Detection Frequency** | Scan interval effectiveness | visibility |
| **Action Rate** | % alerts leading to remediation | efficiency |
| **Alert Deduplication Rate** | reduction of duplicate alerts | reduced noise |
| **Estimated Savings** | cost avoided via shutdowns | ROI of system |

---

## 🏗️ Architecture Overview

SagePulse is a fully serverless ML FinOps control system:

```
EventBridge (every 4 hours)
        ↓
Step Functions Workflow
        ↓
Lambda Scanner
  Scans all SageMaker resources
  Detects idle via CloudWatch (CPU, Invocations)
  Calculates real costs via Pricing API + Cost Explorer
        ↓
DynamoDB
  Stores each idle resource detected
  Prevents duplicate alerts (1 alert per resource per 4h window)
        ↓
SNS Notification
  Sends actionable alert to MLOps team
        ↓
Human Approval (waitForTaskToken)
  Workflow pauses — resumes only after explicit approval
        ↓
Lambda Action
  Stops notebooks (state preserved)
  Notifies about idle endpoints (no auto-deletion)
        ↓
S3
  Archives JSON + Markdown reports for FinOps teams
```

---

## 🧠 Core Design Principle

> "Detect automatically, act only with human validation."

This ensures:

- no accidental shutdown of production ML workloads
- controlled FinOps automation
- safe adoption in enterprise environments

---

## ⚙️ Cost Intelligence Layer

Each resource is evaluated using:

- CPU utilization (CloudWatch)
- invocation activity (SageMaker metrics)
- real cost estimation (AWS Pricing API)
- inactivity duration thresholds

---

## 🔁 Workflow Summary

1. Scheduled scan (EventBridge every 4 hours)
2. Resource discovery (Lambda scanner)
3. Idle detection logic
4. Cost estimation per resource
5. Deduplication (DynamoDB)
6. Alert generation (SNS)
7. Human approval (Step Functions wait state)
8. Optional remediation (stop notebooks)
9. Reporting (S3 archive)

---

## 💰 Business Impact

SagePulse enables:

- up to **40% reduction** in SageMaker development costs
- elimination of unnoticed idle ML workloads
- improved accountability in MLOps environments
- faster detection of cost anomalies

---

## 🔐 Security & Safety Model

- No automatic deletion of resources
- Notebook state preserved (safe stop only)
- Least privilege IAM per component
- No long-lived credentials (OIDC GitHub auth)
- Approval required for any remediation action

---

## 🚀 Setup

```bash
git clone https://github.com/mboumhawahaga-ship-it/SagePulse
cd SagePulse
bash setup.sh
```

The script asks for your email and deploys everything. Requires AWS CLI configured and Terraform installed.

**Infrastructure cost: under $2/month.**

---

## Local Development

```bash
pip install -r requirements.txt -r requirements-dev.txt -r lambda/requirements.txt
pytest tests/ --cov=lambda --cov-fail-under=80 -v
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 · AWS Lambda |
| Orchestration | AWS Step Functions (JSONata, waitForTaskToken) |
| Storage | DynamoDB (alert deduplication) · S3 (reports) |
| Infrastructure | Terraform · S3 remote state · DynamoDB locking |
| CI/CD | GitHub Actions · OIDC auth |
| Observability | AWS Lambda Powertools · CloudWatch custom metrics |
| Testing | pytest · unittest.mock · moto · 88% coverage |

---

## Why I Built This

I built SagePulse during my transition into cloud engineering. SageMaker costs are a real pain point for ML teams — resources stay running, nobody notices, and the bill arrives at the end of the month.

The goal was to build something end-to-end: real AWS infrastructure, real cost data, real notifications, with a human-in-the-loop approval pattern so nothing is ever deleted by accident.

Every technical decision came from hitting a real problem: the `waitForTaskToken` pattern to avoid accidental deletions, DynamoDB deduplication to prevent alert fatigue, OIDC authentication to avoid storing AWS keys in GitHub.

---

Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

MIT — see [LICENSE](LICENSE)
