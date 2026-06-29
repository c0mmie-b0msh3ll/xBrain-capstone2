data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  project     = "tf1-aiops-demo"
  table_name  = "tf1-aiops-audit-demo"
  name_prefix = var.cluster_name

  common_tags = {
    Project     = local.project
    CostCenter  = var.cost_center
    Owner       = var.owner
    Environment = "demo"
    ExpiresAt   = var.expires_at
  }
}

resource "terraform_data" "account_guardrail" {
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.account_id
      error_message = "Refusing to create the demo outside AWS account ${var.account_id}."
    }
  }
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${local.name_prefix}-vpc"
  cidr = "10.42.0.0/16"

  azs                  = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets       = ["10.42.0.0/20", "10.42.16.0/20"]
  enable_dns_hostnames = true

  enable_nat_gateway      = false
  single_nat_gateway      = false
  map_public_ip_on_launch = true

  public_subnet_tags = {
    "kubernetes.io/role/elb"                    = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.kubernetes_version

  cluster_endpoint_public_access = true
  enable_irsa                    = true
  cluster_encryption_config      = {}
  create_kms_key                 = false
  create_cloudwatch_log_group    = false

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  enable_cluster_creator_admin_permissions = true

  eks_managed_node_groups = {
    demo = {
      name           = "${var.cluster_name}-ng"
      subnet_ids     = module.vpc.public_subnets
      instance_types = var.instance_types
      capacity_type  = "ON_DEMAND"
      min_size       = var.min_size
      max_size       = var.max_size
      desired_size   = var.desired_size

      labels = {
        workload = "tf1-aiops-demo"
      }

      tags = {
        Name = "${var.cluster_name}-node"
      }
    }
  }

  depends_on = [terraform_data.account_guardrail]
}

resource "aws_dynamodb_table" "audit" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}

data "aws_iam_policy_document" "triage_engine_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = ["system:serviceaccount:${var.namespace}:tf1-ai-triage-engine"]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "triage_engine" {
  name               = "${var.cluster_name}-triage-engine"
  assume_role_policy = data.aws_iam_policy_document.triage_engine_assume_role.json
}

data "aws_iam_policy_document" "triage_engine" {
  statement {
    sid = "DynamoDbAuditAndIdempotency"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.audit.arn]
  }

  dynamic "statement" {
    for_each = var.agentcore_runtime_arn == "" ? [] : [var.agentcore_runtime_arn]
    content {
      sid     = "InvokeAgentCoreRuntime"
      actions = ["bedrock-agentcore:InvokeAgentRuntime"]
      resources = [
        statement.value,
        "${statement.value}/runtime-endpoint/*",
      ]
    }
  }

  statement {
    sid = "InvokeBedrockQaJudge"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/*",
      "arn:aws:bedrock:${var.aws_region}:${var.account_id}:inference-profile/*",
    ]
  }
}

resource "aws_iam_role_policy" "triage_engine" {
  name   = "tf1-aiops-demo-dynamodb"
  role   = aws_iam_role.triage_engine.id
  policy = data.aws_iam_policy_document.triage_engine.json
}

resource "aws_sns_topic" "budget_alerts" {
  name = "${var.cluster_name}-budget-alerts"
}

resource "aws_sns_topic_subscription" "budget_email" {
  count     = var.budget_alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_alert_email
}

data "aws_iam_policy_document" "budget_sns_publish" {
  statement {
    sid     = "AllowBudgetsPublish"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    resources = [aws_sns_topic.budget_alerts.arn]
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_sns_publish.json
}

resource "aws_budgets_budget" "demo" {
  name         = "${var.cluster_name}-monthly-tagged-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.demo_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Project$%s", local.project)]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 50
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}

resource "aws_cloudwatch_dashboard" "cost_guardrail" {
  dashboard_name = "${var.cluster_name}-cost-guardrail"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 6
        properties = {
          markdown = join("\n", [
            "# TF1 AIOps demo cost guardrail",
            "",
            "Budget: ${var.demo_budget_usd} USD/month for tag `Project=${local.project}`.",
            "CostCenter tag: `${var.cost_center}`.",
            "ExpiresAt tag: `${var.expires_at}`.",
            "",
            "Use Cost Explorer grouped by Service and filtered by tag `Project=${local.project}` for the R&D cost summary.",
          ])
        }
      }
    ]
  })
}
