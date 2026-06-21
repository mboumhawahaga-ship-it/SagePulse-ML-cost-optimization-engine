# EventBridge Schedule → Lambda guardrail toutes les 4h
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.project_name}-schedule"
  description         = "Trigger guardrail Lambda every 4 hours"
  schedule_expression = "rate(4 hours)"

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

resource "aws_cloudwatch_event_target" "guardrail" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "${var.project_name}-guardrail"
  arn       = aws_lambda_function.guardrail.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.guardrail.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
