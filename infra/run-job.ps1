# Run one MovieTech command as a Cloud Run job, wait for it, and show its log.
# The run folder must already be in Cloud Storage under -Prefix (see infra/README.md, step 10).
#
#   .\run-job.ps1 -Prefix output/tony_bruce -Command costs,/data/output/tony_bruce
param(
    [Parameter(Mandatory = $true)] [string] $Prefix,
    [Parameter(Mandatory = $true)] [string[]] $Command
)
$ErrorActionPreference = "Stop"

$out = terraform -chdir="$PSScriptRoot" output -json | ConvertFrom-Json
$job = $out.job.value
$region = $out.region.value

# gcloud takes the container arguments as one comma-separated list, so no argument may contain a comma.
$jobArgs = @($Prefix) + $Command
if ($jobArgs | Where-Object { $_ -like "*,*" }) { throw "An argument contains a comma; gcloud would split it." }

gcloud run jobs execute $job --region $region --args ($jobArgs -join ",") --wait
$ok = $LASTEXITCODE -eq 0

# Newest 80 log lines of the job, printed oldest first. The filter has no quotes in it:
# Windows PowerShell 5.1 strips quotes from arguments passed to gcloud.
$filter = "resource.type=cloud_run_job AND resource.labels.job_name=$job"
$lines = @(gcloud logging read $filter --freshness 2h --limit 80 --format "value(textPayload)")
[array]::Reverse($lines)
$lines

if ($ok) { Write-Host "Job succeeded." } else { Write-Host "Job failed; see the log above."; exit 1 }
