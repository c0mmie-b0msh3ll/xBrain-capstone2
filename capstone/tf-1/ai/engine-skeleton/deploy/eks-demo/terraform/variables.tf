variable "aws_region" {
  description = "AWS region for the EKS demo."
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "Expected AWS account id. Used as a guardrail before creating resources."
  type        = string
  default     = "589077667575"
}

variable "cluster_name" {
  description = "Dedicated EKS cluster name for the TF1 AI Ops demo."
  type        = string
  default     = "tf1-aiops-demo"
}

variable "namespace" {
  description = "Kubernetes namespace used by the demo manifests."
  type        = string
  default     = "tf1-ai-demo"
}

variable "demo_budget_usd" {
  description = "Monthly budget guardrail for resources tagged Project=tf1-aiops-demo."
  type        = number
  default     = 25
}

variable "budget_alert_email" {
  description = "Optional email address to subscribe to the demo budget SNS topic."
  type        = string
  default     = ""
}

variable "expires_at" {
  description = "Expiry date tag for all demo resources, in YYYY-MM-DD format."
  type        = string
}

variable "owner" {
  description = "Owner tag for demo resources."
  type        = string
  default     = "AI"
}

variable "cost_center" {
  description = "CostCenter tag used for R&D cost tracking."
  type        = string
  default     = "RnD"
}

variable "instance_types" {
  description = "Small EKS managed node group instance types. t3.small is enough for the scoped demo."
  type        = list(string)
  default     = ["t3.small"]
}

variable "desired_size" {
  description = "Desired EKS managed node count."
  type        = number
  default     = 2
}

variable "min_size" {
  description = "Minimum EKS managed node count."
  type        = number
  default     = 1
}

variable "max_size" {
  description = "Maximum EKS managed node count."
  type        = number
  default     = 2
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.30"
}

variable "agentcore_runtime_arn" {
  description = "Optional Bedrock AgentCore runtime ARN. When set, the triage engine IRSA role can invoke only this runtime."
  type        = string
  default     = ""
}
