output "cluster_name" {
  value = module.eks.cluster_name
}

output "aws_region" {
  value = var.aws_region
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.audit.name
}

output "triage_engine_role_arn" {
  value = aws_iam_role.triage_engine.arn
}

output "agentcore_runtime_arn" {
  value = var.agentcore_runtime_arn
}

output "budget_name" {
  value = aws_budgets_budget.demo.name
}

output "budget_alerts_topic_arn" {
  value = aws_sns_topic.budget_alerts.arn
}

output "cost_explorer_group_by_service_command" {
  value = "aws ce get-cost-and-usage --region ${var.aws_region} --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD --granularity DAILY --metrics UnblendedCost --filter '{\"Tags\":{\"Key\":\"Project\",\"Values\":[\"tf1-aiops-demo\"]}}' --group-by Type=DIMENSION,Key=SERVICE"
}
