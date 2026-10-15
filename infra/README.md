# Run the pipeline on AWS (Terraform + Fargate)

One pipeline command runs as a Fargate task: the container copies the run folder from S3 into
`/data`, runs the command, and copies the folder back. Nothing runs between commands.

```
laptop ── docker push ──▶ ECR ──▶ ECS Fargate task ──▶ Claude / Gemini APIs
   │                                  │    ▲
   └── aws s3 sync ──▶ S3 run folders ◀┘    └── API keys from Secrets Manager
```

| Resource | Why | Cost (us-east-1, check current pricing) |
|---|---|---|
| S3 bucket | Run folders; private, expires after 30 days | Cents |
| ECR repository | The Docker image; keeps the last 2 | About $0.05 a month for a 0.5 GB image (500 MB free for 12 months on new accounts) |
| Secrets Manager, 2 secrets | Claude and Gemini keys, never in code or state | $0.40 per secret per month, charged by the hour |
| ECS Fargate task, 1 vCPU / 2 GB | Runs one command, then stops | About $0.05 an hour, so under $0.01 for a 10-minute run |
| Public IPv4 for the task | Reaches the APIs without a NAT gateway | $0.005 an hour while it runs |
| CloudWatch Logs | Task output, kept 7 days | Free tier |
| AWS Budgets | Email at 80% of $5 and when the forecast passes $5 | Free |

Built, tried and destroyed the same day: **under $0.10**. Left in place for a month and not used: **about $1**.
Model API calls are billed by Anthropic and Google as usual.

**Why:** Fargate has no 15-minute limit (Lambda does), and the default VPC with a public IP avoids a NAT gateway.
Advanced option: GitHub Actions pushing to ECR through OIDC, and a remote S3 state backend.

## Steps (Windows 11, PowerShell, from the repo folder)

1. **Install the tools** (once):
   ```powershell
   winget install --id Hashicorp.Terraform -e
   winget install --id Amazon.AWSCLI -e
   ```
   Close and reopen PowerShell, then check:
   ```powershell
   terraform version
   aws --version
   ```
   Expected result: `Terraform v1.x` and `aws-cli/2.x`.
2. **Sign in to AWS with an IAM user, not the root account** (once). In the AWS console create an IAM user with `AdministratorAccess`, then *Security credentials → Create access key → Command Line Interface*. Then:
   ```powershell
   aws configure
   ```
   Paste the key ID and secret when asked, region `us-east-1`, output `json`. The keys are stored in `%USERPROFILE%\.aws\credentials`, never in the repo.
   Check:
   ```powershell
   aws sts get-caller-identity
   ```
   Expected result: your 12-digit account ID and an `arn` ending in `user/<your user>`. If it ends in `root`, stop and create the IAM user.
3. **Set your alert email**:
   ```powershell
   Copy-Item infra\terraform.tfvars.example infra\terraform.tfvars
   notepad infra\terraform.tfvars
   ```
   Replace `you@example.com` with your email and save.
4. **Preview** ($0):
   ```powershell
   terraform -chdir=infra init
   terraform -chdir=infra plan
   ```
   Expected result: `Plan: 17 to add, 0 to change, 0 to destroy.`.
5. **Create the resources** (starts billing, see the table above):
   ```powershell
   terraform -chdir=infra apply
   ```
   Type `yes`. Expected result: `Apply complete!` and the outputs (`bucket`, `ecr_url`, ...).
   AWS then emails you to confirm the budget alerts; click the link.
6. **Put the API keys into Secrets Manager** (values come from your environment variables, so they are not typed or saved in history):
   ```powershell
   aws secretsmanager put-secret-value --secret-id movietech/ANTHROPIC_API_KEY --secret-string $env:ANTHROPIC_API_KEY
   aws secretsmanager put-secret-value --secret-id movietech/GEMINI_API_KEY --secret-string $env:GEMINI_API_KEY
   ```
   Expected result: JSON with `"Name": "movietech/..."` and a `VersionId` for each.
7. **Build and push the image**:
   ```powershell
   $ecr = terraform -chdir=infra output -raw ecr_url
   aws ecr get-login-password | docker login --username AWS --password-stdin $ecr.Split('/')[0]
   docker build -t movietech:local .
   docker tag movietech:local "${ecr}:latest"
   docker push "${ecr}:latest"
   ```
   Expected result: `Login Succeeded`, then the push ends with `latest: digest: sha256:...`.
8. **Copy a run folder to S3**:
   ```powershell
   $bucket = terraform -chdir=infra output -raw bucket
   aws s3 sync C:\movietech\output\tony_bruce "s3://$bucket/output/tony_bruce"
   ```
   Expected result: one `upload:` line per file.
9. **Run a command on Fargate**. `costs` calls no model, so this proves the whole path for under a cent:
   ```powershell
   .\infra\run-task.ps1 -Prefix output/tony_bruce -Command costs,/data/output/tony_bruce
   ```
   Expected result after a minute or two: `Downloaded N files`, the cost table, `Uploaded N files`, then `Exit code: 0`.
   If `.ps1` scripts are blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
   If it stops with `ResourceInitializationError`, step 6 or step 7 was skipped.
10. **Copy results back** (for a command that writes files):
    ```powershell
    aws s3 sync "s3://$bucket/output/tony_bruce" C:\movietech\output\tony_bruce_cloud
    ```

## Teardown

```powershell
docker logout (terraform -chdir=infra output -raw ecr_url).Split('/')[0]
terraform -chdir=infra destroy
```
Type `yes`. Expected result: `Removing login credentials ...`, then `Destroy complete! Resources: 17 destroyed.` This deletes the bucket with its runs,
the image, the secrets, the logs and the budget. Your access key stays in `%USERPROFILE%\.aws`;
delete it in the IAM console if you no longer need it.
