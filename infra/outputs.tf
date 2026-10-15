output "bucket" {
  value = google_storage_bucket.data.name
}

output "image_repo" {
  value = local.image_repo
}

output "job" {
  value = google_cloud_run_v2_job.pipeline.name
}

output "region" {
  value = var.region
}
