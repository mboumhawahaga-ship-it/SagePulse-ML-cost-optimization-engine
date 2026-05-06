# EventBridge Rule - Trigger Step Functions weekly on Monday at 8 UTC
resource "aws_cloudwatch_event_rule" "ml_cost_analysis_schedule" {
  name                = "${var.project_name}-schedule"
  description         = "Trigger ML cost analysis every 4 hours"
  schedule_expression = "rate(4 hours)"

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

# EventBridge Target → Step Functions
resource "aws_cloudwatch_event_target" "sfn_target" {
  rule      = aws_cloudwatch_event_rule.ml_cost_analysis_schedule.name
  target_id = "${var.project_name}-sfn-target"
  arn       = aws_sfn_state_machine.ml_cost_optimizer_workflow.arn
  role_arn  = aws_iam_role.eventbridge_sfn_role.arn
}
