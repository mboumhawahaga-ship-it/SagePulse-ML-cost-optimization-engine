output "sns_topic_arn" {
  description = "ARN du topic SNS"
  value       = aws_sns_topic.notifications.arn
}

output "lambda_function_name" {
  description = "Nom de la Lambda guardrail"
  value       = aws_lambda_function.guardrail.function_name
}

output "eventbridge_rule_name" {
  description = "Nom de la règle EventBridge"
  value       = aws_cloudwatch_event_rule.schedule.name
}

output "budget_name" {
  description = "Nom du budget AWS"
  value       = aws_budgets_budget.sagemaker.name
}
