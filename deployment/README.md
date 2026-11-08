# Deploy the Streamlit app

This deployment runs Streamlit on one EC2 server in Stockholm (`eu-north-1`).
Visitors use an HTTPS address and a shared app login. The server calls Bedrock
using its instance role; visitors need neither AWS credentials nor Ollama.

```mermaid
flowchart LR
    Browser -->|HTTPS + shared login| Caddy
    subgraph EC2 t4g.small
        Caddy -->|private Docker network| Streamlit
        Streamlit --> Runs[Saved runs on disk]
    end
    Streamlit --> Bedrock[Bedrock Qwen3 32B]
    Streamlit --> Sources[Yahoo / FRED]
```

Review the files in this order:

1. [`../Dockerfile`](../Dockerfile): the Python app image, running as a non-root user.
2. [`Caddyfile`](Caddyfile): HTTPS, password authentication, and forwarding to Streamlit.
3. [`stack.yaml`](stack.yaml): network, server, instance permissions, and private release storage.
4. [`deploy.py`](deploy.py): package a committed release, configure settings, and send it to the server.
5. [`host.sh`](host.sh): build and health-check a candidate, then replace the running containers.

The app, CLI, research graph, and saved-run format stay shared. `RESEARCH_HOSTED=1`
defaults the UI to Bedrock and hides the local Ollama option. A build argument
records the release's Git revision in `run.json`, without putting `.git` in the image.

## Cost and scope

Approximate monthly cost at 730 hours, before tax, credits, backups, and excess transfer:

| Component | Configuration | USD/month |
| --- | --- | ---: |
| EC2 | `t4g.small`, 2 GB RAM, Arm64, $0.0172/hour | $12.56 |
| Encrypted gp3 disk | 20 GB at $0.0836/GB-month | $1.67 |
| Public IPv4 | One address at $0.005/hour | $3.65 |
| **Server subtotal** | | **$17.88** |
| Bedrock, illustrative | 300–600 runs, each 20k input + 5k output tokens | $1.80–$3.60 |

The Bedrock illustration uses Qwen3 32B rates of $0.15/million input tokens and
$0.60/million output tokens. Actual usage varies with tools, evidence, and review.
Small S3/Parameter Store usage is additional. Recheck [EC2](https://aws.amazon.com/ec2/pricing/on-demand/),
[EBS](https://aws.amazon.com/ebs/pricing/), [public IPv4](https://aws.amazon.com/vpc/pricing/),
and [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) before launching.
Credits can offset the bill temporarily; they do not change these underlying rates.

There is no load balancer, NAT gateway, database, or hosted GPU. Standard CPU credits
avoid surplus CPU charges, but can slow a sustained build or busy server. This is a
single-server deployment with brief interruptions during releases, not high availability.
Stopping the instance still leaves disk and public IPv4 charges.

## Before the first launch

Only run the deployment command after reviewing and merging this PR.

1. In **AWS Settings → View all projects → FinancialResearch → Overview → Additional Info**,
   confirm the selected Region is `eu-north-1`. Check **AWS Settings → Billing** for
   plan status, remaining credits, and any spend limit. The script checks plan status
   but does not change the plan or set a spending cap.
2. Reserve a free hostname at [DuckDNS](https://www.duckdns.org/), for example
   `your-chosen-name.duckdns.org`. No DuckDNS token is needed by this application.
3. Install the local deployment dependencies and refresh your AWS login:

   ```bash
   .venv/bin/python -m pip install -e '.[deploy]'
   aws login --profile financial-research
   ```

4. From a clean, committed checkout, run:

   ```bash
   .venv/bin/python deployment/deploy.py --domain your-chosen-name.duckdns.org
   ```

The default profile and stack name are `financial-research`; the only permitted
Region is `eu-north-1`. The script prompts for a shared username/password and an
optional FRED key. It hashes the password locally with bcrypt, then stores the hash
and optional key in an SSM **SecureString**. Do not paste credentials into GitHub,
the template, or command-line arguments. Without a FRED key, price research and the
offline demo remain available; macro requests report that configuration is missing.

Once the script prints the server's IP address, enter it in DuckDNS and save.
Caddy obtains and renews the certificate once DNS points to the server; ports 80
and 443 must remain reachable. [Caddy's HTTPS documentation](https://caddyserver.com/docs/automatic-https)
explains this process. DNS propagation and certificate issuance can take a few minutes.
The script checks the internal app, not public DNS or certificate issuance.

After the release finishes:

1. Open `https://your-chosen-name.duckdns.org`. Confirm it asks for a login and rejects a wrong password.
2. Run the **Offline demo** and download the report, state, and diagnostics.
3. Select **Amazon Bedrock**, run one short AAPL question with two tool calls and
   review disabled, and inspect its evidence, report, and model usage.

Share the app login only with intended visitors. Authenticated visitors can trigger
paid inference; the tool-call budget bounds one run, not aggregate monthly spend.

## What the release does

`deploy.py` archives only committed application/deployment paths. Personal notes,
`.aws`, `.git`, and `runs` are outside the release. It uploads this archive to a
private, encrypted, versioned S3 bucket, then uses Systems Manager to download it,
verify its SHA-256, and run `host.sh`. No GitHub token is installed on the server.

The instance role has the standard `AmazonSSMManagedInstanceCore` administration
permissions, plus access to the release prefix, runtime settings, and the selected
Qwen foundation model. The SSM managed policy includes broader Parameter Store
reads; the additional runtime policy scopes this app's settings and decrypt operation.
The Bedrock action was derived using IAM Policy Autopilot, then narrowed to
`bedrock:InvokeModel` for the model actually used. No guardrails, bearer-token
invocation, or built-in server tools are requested by this adapter.

The security group exposes only HTTP/HTTPS. Streamlit's port 8501 is internal;
SSH is closed. IMDSv2 supplies short-lived server credentials, including through
the container network. No personal AWS login cache is copied into the image.

The host keeps settings in root-readable files and run artifacts in
`/var/lib/financial-research/runs`. The app container has a read-only filesystem
except for run storage and temporary caches. Caddy's certificates persist separately.
The candidate must pass Streamlit's health check before replacing the app. This
check verifies startup; it does not prove that Bedrock or data providers are reachable.

## Updates, troubleshooting, and recovery

After merging a change, repeat the same deployment command from the new commit.
Updates preserve the original AMI, so a routine app release does not replace the
server. Operating-system/AMI upgrades need a separate reviewed maintenance change.
Use `--configure` when intentionally replacing the shared login or FRED settings.

If a release fails, the script exits unsuccessfully and prints its SSM command ID.
Inspect the result without SSH:

```bash
aws ssm get-command-invocation --profile financial-research --region eu-north-1 \
  --command-id COMMAND_ID --instance-id INSTANCE_ID
aws ssm start-session --profile financial-research --region eu-north-1 \
  --target INSTANCE_ID
```

Interactive sessions need the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).
On the server, inspect `docker logs financial-research-app`,
`docker logs financial-research-proxy`, and `/var/log/cloud-init-output.log`.
Settings must not be included in diagnostic output shared with others.

A candidate build/health failure leaves the previous app running. Later failures
during container replacement require operator recovery. The previous source release
path is saved in `/var/lib/financial-research/previous-release`; run its `host.sh`
through SSM with the same parameter name, hostname, and original Git revision to
rebuild it. This is a manual rollback, not automatic deployment recovery.

Run files survive container updates and reboots. They are local files, not an
off-server backup or LangGraph resume checkpoints. Back up important runs before
replacing the instance. A replacement does **not** automatically mount the old disk.

Deleting the stack leaves the S3 bucket and root disk retained, to avoid silently
deleting research. These continue to incur storage charges. The runtime SSM
parameter is also outside the stack. Before cleanup, decide which runs/releases to
keep, copy them somewhere safe, then explicitly remove the retained resources if
they are no longer needed. On a long-lived server, remove obsolete Docker build
caches/images after checking which current and previous releases you want to keep.

## Verification

The normal pytest suite tests packaging, password hashing, failed-release reporting,
AMI preservation, and the hosted UI without calling AWS. CI also runs pinned
`cfn-lint` and Guard checks, builds the image, checks its health endpoint, and runs
an offline research command inside it. `security.guard` contains four project rules;
it is not a comprehensive compliance assessment.

Local ARM64 checks also verified HTTPS using Caddy's localhost certificate, missing
and wrong passwords returning 401, an authenticated Streamlit WebSocket handshake,
and saved demo files surviving a container restart. AWS's read-only `ValidateTemplate`
accepted the template; this does not verify provisioning permissions or live connectivity.

Creating the stack, obtaining a public certificate, accessing Bedrock from the
instance role, and testing a complete browser run require the first live deployment.
