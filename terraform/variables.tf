variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "ml-cost-optimizer"
}

variable "notification_email" {
  description = "Email for SNS notifications"
  type        = string
  sensitive   = true
}

variable "budget_limit_usd" {
  description = "Monthly SageMaker budget limit in USD"
  type        = string
  default     = "100"
}

variable "high_threshold" {
  description = "Cost threshold (USD/mo) above which a prod resource triggers an escalation alert"
  type        = number
  default     = 50
}

variable "dry_run" {
  description = "When true the Lambda reports every decision but never stops anything"
  type        = bool
  default     = true
}

variable "idle_window_hours" {
  description = "Look-back window (hours) for CloudWatch idle detection"
  type        = number
  default     = 24
}

variable "log_retention_days" {
  description = "Retention of the Lambda log group"
  type        = number
  default     = 14
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the deploy role via OIDC (owner/name)"
  type        = string
  default     = "mboumhawahaga-ship-it/SagePulse-ML-cost-optimization-engine"
}

variable "idle_cost_threshold" {
  description = "Minimum monthly cost (USD) for a dev idle resource to be auto-stopped"
  type        = number
  default     = 10
}
