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

variable "idle_cost_threshold" {
  description = "Minimum monthly cost (USD) for a dev idle resource to be auto-stopped"
  type        = number
  default     = 10
}
