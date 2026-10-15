variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Where AWS Budgets sends cost alerts. Set it in terraform.tfvars (git-ignored)."
  type        = string
}

variable "budget_usd" {
  description = "Monthly AWS budget; alerts at 80% actual and 100% forecast."
  type        = number
  default     = 5
}

variable "task_cpu" {
  description = "Fargate vCPU units (1024 = 1 vCPU)."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate memory in MiB."
  type        = number
  default     = 2048
}

variable "image_tag" {
  description = "Image tag the task runs; push this tag to ECR before running."
  type        = string
  default     = "latest"
}
