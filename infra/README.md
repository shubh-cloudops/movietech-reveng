# Run the pipeline on Google Cloud (Terraform + Cloud Run)

One pipeline command runs as a Cloud Run job: the container copies the run folder from Cloud
Storage into `/data`, runs the command, and copies the folder back. Nothing runs between commands.

```
laptop ── docker push ──▶ Artifact Registry ──▶ Cloud Run job ──▶ Claude / Gemini APIs
   │                                              │    ▲
   └── gcloud storage rsync ──▶ Cloud Storage ◀───┘    └── API keys from Secret Manager
```

Everything here sits inside Google Cloud's always-free limits (monthly, us-central1, checked
2026-10-15 against cloud.google.com/run/pricing and cloud.google.com/free; limits can change):

| Resource | Why | Free limit | What MovieTech uses |
|---|---|---|---|
| Cloud Run job, 1 vCPU / 2 GiB | Runs one command, then stops | 240,000 vCPU-seconds and 450,000 GiB-seconds | About 60 hours of runs a month before any charge |
| Artifact Registry | The Docker image | 0.5 GB | One image kept; CI fails if it passes 450 MB |
| Cloud Storage bucket | Run folders; private, deleted after 30 days | 5 GB in us-central1, us-east1, us-west1 only | A few MB per run |
| Secret Manager | Claude and Gemini keys, never in code or state | 6 secret versions, 10,000 reads | 2 versions, 2 reads per run |
| Cloud Logging | Job output, kept 30 days | 50 GiB | KB per run |
| Network out to the internet | Calls to the Claude and Gemini APIs | 1 GiB in North America | KB per call |
| Billing budget + email channel | Email at 80% of $1 and when the forecast passes $1 | Free | |

Expected bill: **$0**. Model API calls are billed by Anthropic and Google as usual.

Keep it at $0:
- Stay on the free trial: Google never charges a free-trial account until you click **Activate full account**; don't.
- Keep `region = "us-central1"`; other regions have no free Cloud Storage.
- When you replace an API key, disable the old version (`gcloud secrets versions disable 1 --secret ...`) so you stay under 6.
- Run `terraform destroy` when you are done (see Teardown).

**Why:** a Cloud Run job has no 15-minute limit and bills only while it runs, and the pipeline sits in its own project so teardown never touches the repair app's Firebase project.
Advanced option: GitHub Actions pushing images through Workload Identity Federation, and a remote Cloud Storage state backend.

## Steps (Windows 11, PowerShell, from the repo folder)

1. **Install the tools** (once):
   ```powershell
   winget install --id Hashicorp.Terraform -e
   winget install --id Google.CloudSDK -e
   ```
   Close and reopen PowerShell, then check:
   ```powershell
   terraform version
   gcloud version
   ```
   Expected result: `Terraform v1.x` and `Google Cloud SDK 5xx.x.x`.
   If `gcloud` is not recognised, open a new PowerShell window; the installer adds it to PATH.
2. **Sign in** (once). Each command opens the browser; pick your Google account and allow access:
   ```powershell
   gcloud auth login
   gcloud auth application-default login
   ```
   Expected result: `You are now logged in as [...]`, then `Credentials saved to file: [...\application_default_credentials.json]`.
   The first is for gcloud, the second is what Terraform uses. Both are stored in `%APPDATA%\gcloud`, never in the repo.
3. **Create a project for MovieTech and link billing** (once). Project IDs are global, so this adds a random number:
   ```powershell
   $project = "movietech-$(Get-Random -Minimum 10000 -Maximum 99999)"
   gcloud projects create $project --name "MovieTech pipeline"
   gcloud config set project $project
   gcloud billing accounts list
   ```
   Expected result: `Create in progress for [...]`, `Updated property [core/project]`, then a table with your billing account `ACCOUNT_ID` like `0X0X0X-0X0X0X-0X0X0X`.
   If the table is empty, create a billing account at https://console.cloud.google.com/billing and run the last command again.
   ```powershell
   gcloud billing projects link $project --billing-account 0X0X0X-0X0X0X-0X0X0X
   gcloud services enable cloudresourcemanager.googleapis.com
   gcloud auth application-default set-quota-project $project
   $project
   ```
   Use your own account ID. Expected result: `billingEnabled: true`, `Operation ... finished successfully`, `Quota project "movietech-..." was added to ADC`, then your project ID. Note it down.
   The quota project line stops the "Cannot find a quota project" warnings.
   Terraform enables the other APIs itself; it needs this one first to read the project.
4. **Fill in your settings**:
   ```powershell
   Copy-Item infra\terraform.tfvars.example infra\terraform.tfvars
   notepad infra\terraform.tfvars
   ```
   Set `project_id` (from step 3), `billing_account` (from step 3) and `alert_email`, then save.
   If your billing account is not in USD, set `budget_amount` in that currency (for example `85` for INR).
5. **Preview** ($0):
   ```powershell
   terraform -chdir=infra init
   terraform -chdir=infra plan
   ```
   Expected result: `Plan: 19 to add, 0 to change, 0 to destroy.`
6. **Create the image registry and the empty secrets first** ($0). The job in step 9 needs the image and the keys to exist:
   ```powershell
   terraform -chdir=infra apply -target="google_artifact_registry_repository.images" -target="google_secret_manager_secret.api"
   ```
   Type `yes`. Expected result: a warning about `-target` (normal here), then `Apply complete! Resources: 11 added`.
7. **Put the API keys into Secret Manager**. The value goes through a temporary file with no line break and comes from your environment variables, so the key is not typed or kept in history:
   ```powershell
   $tmp = Join-Path $env:TEMP "movietech-key.txt"
   [IO.File]::WriteAllText($tmp, $env:ANTHROPIC_API_KEY)
   gcloud secrets versions add movietech-anthropic-api-key --data-file $tmp
   [IO.File]::WriteAllText($tmp, $env:GEMINI_API_KEY)
   gcloud secrets versions add movietech-gemini-api-key --data-file $tmp
   Remove-Item $tmp
   ```
   Expected result: `Created version [1] of the secret [...]` for each.
   If you see `Value cannot be null`, the environment variable is not set in this window; set it and repeat.
8. **Build and push the image** (Docker Desktop running):
   ```powershell
   $project = gcloud config get-value project
   $repo = "us-central1-docker.pkg.dev/$project/movietech"
   gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
   docker build -t movietech:local .
   docker tag movietech:local "${repo}/movietech:latest"
   docker push "${repo}/movietech:latest"
   ```
   Expected result: `Docker configuration file updated`, then the push ends with `latest: digest: sha256:...`.
   If you changed `region`, use it in place of `us-central1` on these lines.
9. **Create the rest** (the bucket, the job, its permissions and the budget):
   ```powershell
   terraform -chdir=infra apply
   ```
   Type `yes`. Expected result: `Apply complete! Resources: 8 added` and the outputs (`bucket`, `image_repo`, `job`, `region`).
   If it fails with `Image ... not found`, step 8 did not finish; if it mentions a secret version, step 7 did not.
10. **Copy a run folder to Cloud Storage**:
    ```powershell
    $bucket = terraform -chdir=infra output -raw bucket
    gcloud storage rsync -r C:\movietech\output\tony_bruce "gs://$bucket/output/tony_bruce"
    ```
    Expected result: one `Copying file://...` line per file.
11. **Run a command on Cloud Run**. `costs` calls no model, so this proves the whole path for $0:
    ```powershell
    .\infra\run-job.ps1 -Prefix output/tony_bruce -Command costs,/data/output/tony_bruce
    ```
    Expected result after a minute or two: `Execution [...] has successfully completed`, then log lines with `Downloaded N files`, the cost table, `Uploaded N files`, and `Job succeeded.`
    If `.ps1` scripts are blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
    If no log lines show, wait 30 seconds and look in the console under Cloud Run, Jobs, movietech, Logs; logs can arrive late.
12. **Copy results back** (for a command that writes files):
    ```powershell
    gcloud storage rsync -r "gs://$bucket/output/tony_bruce" C:\movietech\output\tony_bruce_cloud
    ```

## Teardown

```powershell
terraform -chdir=infra destroy
```
Type `yes`. Expected result: `Destroy complete! Resources: 19 destroyed.` This deletes the bucket
with its runs, the image repository with its images, the secrets, the job, the service account and
the budget. The APIs stay enabled, which costs nothing.

To remove every trace, delete the project as well (it can be restored for 30 days):
```powershell
gcloud projects delete (gcloud config get-value project)
```
Type `Y`. Expected result: `Deleted [https://cloudresourcemanager.googleapis.com/v1/projects/...]`.
