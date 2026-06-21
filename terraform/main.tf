terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "ml-cost-optimizer-tfstate"
    key            = "ml-cost-optimizer/terraform.tfstate"
    region         = "eu-west-1"
    encrypt        = true
    dynamodb_table = "ml-cost-optimizer-tflock"
  }
}

provider "aws" {
  region = var.aws_region
}

# SNS Topic pour les notifications
resource "aws_sns_topic" "notifications" {
  name = "${var.project_name}-notifications"

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# Lambda Guardrail
resource "aws_lambda_function" "guardrail" {
  filename         = "../lambda/function.zip"
  function_name    = "${var.project_name}-guardrail"
  role             = aws_iam_role.lambda_role.arn
  handler          = "guardrail.handler"
  source_code_hash = filebase64sha256("../lambda/function.zip")
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      SNS_TOPIC_ARN        = aws_sns_topic.notifications.arn
      HIGH_THRESHOLD       = tostring(var.high_threshold)
      IDLE_COST_THRESHOLD  = tostring(var.idle_cost_threshold)
    }
  }

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

resource "aws_cloudwatch_log_group" "guardrail" {
  name              = "/aws/lambda/${aws_lambda_function.guardrail.function_name}"
  retention_in_days = 7

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}
