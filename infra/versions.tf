terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Signed in with your own account (gcloud auth application-default login), the Budgets API
  # needs a project to bill the API calls to. This uses the MovieTech project.
  user_project_override = true
  billing_project       = var.project_id

  # Every resource gets these labels, so the bill and the console show what belongs to MovieTech.
  default_labels = {
    project    = "movietech"
    managed-by = "terraform"
  }
}
