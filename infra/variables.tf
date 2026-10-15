variable "project_id" {
  description = "The GCP project created for MovieTech (infra/README.md, step 3)."
  type        = string
}

variable "billing_account" {
  description = "Billing account ID from `gcloud billing accounts list`, for the budget alert."
  type        = string
}

variable "alert_email" {
  description = "Where budget alerts go. Set it in terraform.tfvars (git-ignored)."
  type        = string
}

variable "region" {
  description = "Region for every resource. us-central1, us-east1 and us-west1 have free Cloud Storage."
  type        = string
  default     = "us-central1"
}

variable "budget_amount" {
  description = "Monthly budget in your billing account's currency. 1 USD (about 85 INR) means any charge at all is emailed."
  type        = number
  default     = 1
}

variable "job_cpu" {
  description = "vCPUs for each run."
  type        = string
  default     = "1"
}

variable "job_memory" {
  description = "Memory for each run."
  type        = string
  default     = "2Gi"
}

variable "image_tag" {
  description = "Image tag the job runs; push this tag to Artifact Registry before step 9."
  type        = string
  default     = "latest"
}
