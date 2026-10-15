# Run one MovieTech command on AWS Fargate, wait for it, and show its log.
# The run folder must already be in S3 under -Prefix (see infra/README.md, step 8).
#
#   .\run-task.ps1 -Prefix output/tony_bruce -Command costs,/data/output/tony_bruce
param(
    [Parameter(Mandatory = $true)] [string] $Prefix,
    [Parameter(Mandatory = $true)] [string[]] $Command
)
$ErrorActionPreference = "Stop"

$out = terraform -chdir="$PSScriptRoot" output -json | ConvertFrom-Json
$cluster = $out.cluster.value

# Overrides go through a file: Windows PowerShell 5.1 strips the quotes from inline JSON.
$overridesFile = Join-Path $PSScriptRoot "overrides.json"
@{ containerOverrides = @(@{ name = "movietech"; command = @($Prefix) + $Command }) } |
    ConvertTo-Json -Depth 5 -Compress | Set-Content -Encoding ascii $overridesFile

$network = "awsvpcConfiguration={subnets=[$($out.subnet_ids.value -join ',')]," +
           "securityGroups=[$($out.security_group_id.value)],assignPublicIp=ENABLED}"

$task = aws ecs run-task --cluster $cluster --launch-type FARGATE `
    --task-definition $out.task_definition.value --network-configuration $network `
    --overrides "file://$overridesFile" --query "tasks[0].taskArn" --output text
if (-not $task -or $task -eq "None") { throw "run-task did not start a task; see the error above." }
Write-Host "Started $task"

aws ecs wait tasks-stopped --cluster $cluster --tasks $task
$exit = aws ecs describe-tasks --cluster $cluster --tasks $task `
    --query "tasks[0].containers[0].exitCode" --output text
$reason = aws ecs describe-tasks --cluster $cluster --tasks $task `
    --query "tasks[0].stoppedReason" --output text

aws logs tail $out.log_group.value --since 30m
Write-Host "Exit code: $exit. Stopped reason: $reason"
