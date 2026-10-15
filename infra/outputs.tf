output "bucket" {
  value = aws_s3_bucket.data.bucket
}

output "ecr_url" {
  value = aws_ecr_repository.app.repository_url
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "task_definition" {
  value = aws_ecs_task_definition.pipeline.family
}

output "subnet_ids" {
  value = data.aws_subnets.default.ids
}

output "security_group_id" {
  value = aws_security_group.task.id
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
