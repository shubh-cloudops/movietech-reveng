# MovieTech pipeline on GCP: run one pipeline command at a time as a Cloud Run job.
# Nothing runs or bills by the hour between runs. `terraform destroy` removes everything,
# including stored runs and images.

locals {
  name       = "movietech"
  image_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"

  # Environment variable the pipeline reads => Secret Manager secret ID.
  secrets = {
    ANTHROPIC_API_KEY = "${local.name}-anthropic-api-key"
    GEMINI_API_KEY    = "${local.name}-gemini-api-key"
  }
}

data "google_project" "this" {}

# ---------- APIs ----------
# Left on at destroy: an enabled API costs nothing, and turning one off can break other things.

resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ---------- Storage: run folders (inputs and outputs) ----------

resource "google_storage_bucket" "data" {
  name                        = "${local.name}-data-${var.project_id}"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  force_destroy               = true # teardown also deletes stored runs
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------- Image registry ----------

resource "google_artifact_registry_repository" "images" {
  repository_id          = local.name
  location               = var.region
  format                 = "DOCKER"
  description            = "MovieTech pipeline image"
  cleanup_policy_dry_run = false

  # Keep the 2 newest images and delete the rest, so storage stays inside the free 0.5 GB.
  cleanup_policies {
    id     = "keep-last-2"
    action = "KEEP"
    most_recent_versions {
      keep_count = 2
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------- API keys ----------
# Terraform creates the empty secrets only. You add the values with gcloud, so the keys never
# appear in .tf files or in Terraform state.

resource "google_secret_manager_secret" "api" {
  for_each  = local.secrets
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# ---------- Permissions ----------
# The job runs as its own service account that can touch only this bucket and these secrets.

resource "google_service_account" "job" {
  account_id   = "${local.name}-job"
  display_name = "MovieTech pipeline job"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "job" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser" # list, read, write and overwrite objects
  member = "serviceAccount:${google_service_account.job.email}"
}

resource "google_secret_manager_secret_iam_member" "job" {
  for_each  = google_secret_manager_secret.api
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.job.email}"
}

# ---------- Compute ----------
# Logs go to Cloud Logging on their own (free up to 50 GiB a month, kept 30 days).

resource "google_cloud_run_v2_job" "pipeline" {
  name                = local.name
  location            = var.region
  deletion_protection = false # so teardown can delete it

  template {
    task_count = 1

    template {
      service_account = google_service_account.job.email
      timeout         = "3600s" # a War Room run can take a while
      max_retries     = 0       # a retry would pay for the same model calls twice

      containers {
        image   = "${local.image_repo}/${local.name}:${var.image_tag}"
        command = ["python", "-m", "movietech.cloud"]
        args    = ["output/demo", "--help"] # each run replaces this (see run-job.ps1)

        resources {
          limits = {
            cpu    = var.job_cpu
            memory = var.job_memory
          }
        }

        env {
          name  = "MOVIETECH_BUCKET"
          value = google_storage_bucket.data.name
        }

        dynamic "env" {
          for_each = google_secret_manager_secret.api
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value.secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  # The job can only start once it may read the secrets and the bucket.
  depends_on = [google_secret_manager_secret_iam_member.job, google_storage_bucket_iam_member.job]
}

# ---------- Cost guard ----------

resource "google_monitoring_notification_channel" "email" {
  display_name = "MovieTech budget alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  depends_on = [google_project_service.apis]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "${local.name}-monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      units = tostring(var.budget_amount) # in the billing account's own currency
    }
  }

  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
  }

  depends_on = [google_project_service.apis]
}
