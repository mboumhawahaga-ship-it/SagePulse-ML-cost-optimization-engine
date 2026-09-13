# IAM role assumed by GitHub Actions through OIDC — no long-lived AWS keys in GitHub.
# Only pushes to main of var.github_repository can assume it.

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"] # pragma: allowlist secret
}

resource "aws_iam_role" "github_actions_deploy" {
  name = "${var.project_name}-github-deploy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
          }
        }
      }
    ]
  })

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

# What CI is allowed to do: ship new Lambda code. Nothing else.
# Infrastructure changes (SNS, IAM, budgets, this role) are applied by an
# operator with `terraform apply` — CI never holds those permissions.
resource "aws_iam_role_policy" "github_actions_deploy_policy" {
  name = "${var.project_name}-github-deploy-policy"
  role = aws_iam_role.github_actions_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "UpdateGuardrailCode"
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-guardrail"
      }
    ]
  })
}

output "github_deploy_role_arn" {
  description = "OIDC role ARN — set it as the AWS_DEPLOY_ROLE_ARN GitHub secret"
  value       = aws_iam_role.github_actions_deploy.arn
}
