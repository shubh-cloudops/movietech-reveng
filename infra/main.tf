# MovieTech pipeline on AWS: run one pipeline command at a time as a Fargate task.
# Nothing runs or bills by the hour between runs except the two secrets ($0.80 a month) and
# image storage in ECR. `terraform destroy` removes everything, including stored runs.

locals {
  name = "movietech"
}

data "aws_caller_identity" "me" {}

# The default VPC and its public subnets: no NAT gateway (about $32 a month), the task gets a
# public IP for the run instead and accepts no inbound traffic.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# ---------- Storage: run folders (inputs and outputs) ----------

resource "aws_s3_bucket" "data" {
  bucket        = "${local.name}-data-${data.aws_caller_identity.me.account_id}"
  force_destroy = true # teardown also deletes stored runs
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-old-runs"
    status = "Enabled"
    filter {
      prefix = ""
    }
    expiration {
      days = 30
    }
  }
}

# ---------- Image registry ----------

resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the last 2 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 2 }
      action       = { type = "expire" }
    }]
  })
}

# ---------- API keys ----------
# Terraform creates the empty secrets only. You put the values in with the AWS CLI, so the keys
# never appear in .tf files or in Terraform state.

resource "aws_secretsmanager_secret" "api" {
  for_each                = toset(["ANTHROPIC_API_KEY", "GEMINI_API_KEY"])
  name                    = "${local.name}/${each.key}"
  recovery_window_in_days = 0 # delete at once on destroy, so a rebuild can reuse the name
}

# ---------- Logs ----------

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 7
}

# ---------- Permissions ----------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: what ECS itself needs to start the task (pull the image, write logs, read keys).
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.api : s.arn]
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "read-api-keys"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

# Task role: what the pipeline code may do once running (only this bucket).
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "bucket" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }
}

resource "aws_iam_role_policy" "bucket" {
  name   = "run-folders"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.bucket.json
}

# ---------- Compute ----------

resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_security_group" "task" {
  name        = "${local.name}-task"
  description = "Outbound only: model APIs, S3, ECR. No inbound."
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_task_definition" "pipeline" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name       = local.name
    image      = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential  = true
    entryPoint = ["python", "-m", "movietech.cloud"]
    command    = ["output/demo", "--help"] # each run replaces this (see run-task.ps1)
    environment = [
      { name = "MOVIETECH_BUCKET", value = aws_s3_bucket.data.bucket }
    ]
    secrets = [for key, s in aws_secretsmanager_secret.api : { name = key, valueFrom = s.arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "run"
      }
    }
  }])
}

# ---------- Cost guard ----------

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
