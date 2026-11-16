"""Deploy a committed release after reviewing the infrastructure and its costs."""

import argparse
import getpass
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

import bcrypt
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
REGION = "eu-north-1"
# Package a committed whitelist, never the working directory or personal notes.
RELEASE_PATHS = (
    "Dockerfile", ".dockerignore", "pyproject.toml", "requirements.lock",
    "src", ".streamlit", "deployment",
)


def bundle(root: Path, destination: Path) -> tuple[str, str]:
    """Write selected files from the local Git HEAD into a release archive.

    Args:
        root: Local repository directory in which to run Git.
        destination: Local .tar.gz file to create, such as /tmp/release.tar.gz.

    Returns:
        (revision, digest): the full Git commit ID and the hexadecimal SHA-256
        of the archive's bytes. The digest checks the downloaded file's integrity;
        it is not a hash of the destination path.

    Uncommitted changes to tracked release files cause packaging to fail.
    """
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()

    # The archive comes from HEAD. Reject edits to tracked release files so
    # the deployed version cannot silently differ from those local edits.
    subprocess.run(
        ["git", "diff", "--exit-code", "HEAD", "--", *RELEASE_PATHS],
        cwd=root, check=True, capture_output=True,
    )
    # git archive packages the files stored in this commit without changing the checkout.
    # stdout=output writes the compressed archive bytes into the destination file.
    with destination.open("wb") as output:
        subprocess.run(
            ["git", "archive", "--format=tar.gz", revision, "--", *RELEASE_PATHS],
            cwd=root, check=True, stdout=output,
        )

    # EC2 checks this digest before extracting or running the downloaded code.
    return revision, hashlib.sha256(destination.read_bytes()).hexdigest()


def configure(ssm, name: str, *, replace: bool = False) -> None:
    """Ensure the application's encrypted runtime settings exist in Parameter Store.

    Args:
        ssm: Boto3 Systems Manager client for the target project and Region.
        name: Full parameter name, e.g. /financial-research/financial-research/runtime.
        replace: Whether to prompt for and overwrite existing settings (--configure).

    Reuse an existing SecureString unless replacement was requested. Otherwise,
    prompt for the visitor login and optional FRED API key, hash the password,
    and save the settings for host.sh. This configures app secrets, not the AWS
    CLI profile, and does not fetch FRED data. It has no return value.
    """
    try:
        existing = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
    except ssm.exceptions.ParameterNotFound:
        existing = None

    # Routine releases reuse the login and FRED key. Only --configure replaces them.
    if existing and not replace:
        if existing["Type"] != "SecureString":
            raise ValueError("The runtime settings parameter must be a SecureString.")
        return

    username = input("Shared app username [research]: ").strip() or "research"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", username):
        raise ValueError("Use 1–32 letters, digits, dots, underscores, or hyphens for the username.")

    # getpass prompts for a secret without showing the characters typed in the terminal.
    password = getpass.getpass("Shared app password (12–72 bytes): ")
    # bcrypt's input limit is measured in bytes, including multibyte characters.
    if not 12 <= len(password.encode()) <= 72:
        raise ValueError("Use a password containing 12–72 UTF-8 bytes.")
    if password != getpass.getpass("Repeat app password: "):
        raise ValueError("Passwords did not match.")

    fred_key = getpass.getpass("FRED API key (optional; Enter to skip): ").strip()
    if fred_key and not re.fullmatch(r"[A-Fa-f0-9]{32}", fred_key):
        raise ValueError("Expected a 32-character FRED API key.")
    settings = {
        "username": username,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "fred_api_key": fred_key,
    }
    # Store a password hash, not the password. SecureString encrypts the settings at rest.
    ssm.put_parameter(Name=name, Type="SecureString", Value=json.dumps(settings), Overwrite=True)


def ensure_stack(cfn, name: str, template: str, domain: str, image_id: str) -> dict[str, str]:
    """Create or update a named CloudFormation stack and wait for completion.

    Args:
        cfn: Boto3 CloudFormation client.
        name: Deployed stack name, independent of the template's filename.
        template: YAML template contents read from stack.yaml.
        domain: Public app hostname supplied as the DomainName template parameter.
        image_id: Amazon Linux image (AMI) for initial creation; updates keep the old AMI.

    Returns:
        Template output names mapped to values, including InstanceId,
        ArtifactBucket, and PublicIp for the remaining deployment steps.
    """
    options = {
        "StackName": name,
        "TemplateBody": template,
        # Acknowledge that the template creates an IAM role for the server.
        "Capabilities": ["CAPABILITY_IAM"],
        "Parameters": [
            {"ParameterKey": "DomainName", "ParameterValue": domain},
            {"ParameterKey": "ImageId", "ParameterValue": image_id},
        ],
        "Tags": [{"Key": "Project", "Value": "FinancialResearch"}],
    }
    try:
        cfn.create_stack(**options)
        waiter = "stack_create_complete"
    except cfn.exceptions.AlreadyExistsException:
        # An app release must not replace the server just because a new AMI exists.
        options["Parameters"][1] = {"ParameterKey": "ImageId", "UsePreviousValue": True}
        try:
            cfn.update_stack(**options)
            waiter = "stack_update_complete"
        except ClientError as exc:
            # CloudFormation has no modeled exception for an unchanged template.
            # An unchanged stack is expected on an app-only release; other errors must fail.
            if (
                exc.response["Error"].get("Code") != "ValidationError"
                or "No updates are to be performed" not in exc.response["Error"].get("Message", "")
            ):
                raise
            waiter = None

    # Creating/updating a stack is asynchronous. Wait before using its resources.
    if waiter:
        cfn.get_waiter(waiter).wait(StackName=name, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    return {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}


def wait_for_ssm(ssm, instance: str) -> None:
    """Wait until the target EC2 instance is online in Systems Manager.

    ssm is the Boto3 Systems Manager client; instance is an EC2 instance ID.
    Return without a value when ready, or raise TimeoutError if it never connects.
    """
    # EC2 can exist before its SSM Agent connects. Wait until it can receive commands.
    for _ in range(90):
        info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance]}])
        if any(item["PingStatus"] == "Online" for item in info["InstanceInformationList"]):
            return
        time.sleep(5)
    raise TimeoutError("The instance did not register with Systems Manager; inspect its bootstrap logs.")


def release_command(bucket: str, key: str, digest: str, revision: str, parameter: str, domain: str) -> str:
    """Build the shell script that SSM will execute on EC2; do not execute it locally.

    Args:
        bucket: S3 bucket containing the uploaded release archive.
        key: Archive's object path inside that bucket (releases/<commit>/<digest>.tar.gz).
        digest: Expected SHA-256 of the archive bytes, also used for the EC2 release folder.
        revision: Git commit ID to record in the app's run diagnostics.
        parameter: Parameter Store name from which host.sh loads the runtime settings.
        domain: Public hostname passed to host.sh for Caddy's HTTPS configuration.

    Return shell commands to download, verify, and extract the archive, then run host.sh.
    """
    # This shell script runs on EC2 through SSM; deploy.py itself runs on your Mac.
    directory = f"/opt/financial-research/releases/{digest}"
    # These values become shell arguments on the server, so quote each one.
    q = shlex.quote
    return "\n".join([
        "set -eu",
        "umask 077",
        f"export AWS_DEFAULT_REGION={REGION}",
        # The boot script installs Docker; finish that before installing the app.
        "cloud-init status --wait",
        f"mkdir -p {q(directory)}",
        f"aws s3 cp {q(f's3://{bucket}/{key}')} {q(directory + '/release.tar.gz')}",
        f"cd {q(directory)}",
        # Verify the archive matches the local release before trusting its contents.
        f"printf '%s\\n' {q(digest + '  release.tar.gz')} | sha256sum --check --status",
        "tar --extract --gzip --file release.tar.gz --no-same-owner --no-same-permissions",
        # The source now exists on EC2. host.sh handles secrets and containers.
        f"bash deployment/host.sh {q(parameter)} {q(domain)} {q(revision)}",
    ])


def main(argv=None) -> int:
    """Read CLI options and coordinate packaging on this computer and installation on EC2.

    argv is an optional argument list for tests; None reads the terminal arguments.
    Return 0 on success or 1 on a deployment failure. Invalid CLI arguments exit via argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domain", required=True,
        help="Existing public hostname, e.g. your-name.duckdns.org; DNS is configured separately",
    )
    parser.add_argument(
        "--stack", default="financial-research",
        help="Name of the deployed CloudFormation stack; its template is deployment/stack.yaml",
    )
    parser.add_argument(
        "--profile", default="financial-research",
        help="Local AWS CLI profile selecting the deployment login/settings from ~/.aws/config",
    )
    parser.add_argument(
        "--region", choices=[REGION], default=REGION,
        help="Region for project resources; this project uses Stockholm",
    )
    parser.add_argument(
        "--configure", action="store_true",
        help="Prompt for and replace the saved visitor login and optional FRED API key",
    )
    args = parser.parse_args(argv)

    # Validate values used in resource names and proxy configuration before calling AWS.
    domain = args.domain.lower()
    if len(domain) > 253 or not re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain,
    ):
        parser.error("Enter a hostname without a scheme, port, or path.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", args.stack):
        parser.error("Use a CloudFormation stack name beginning with a letter and containing letters, digits, or hyphens.")

    try:
        # Boto3 is AWS's Python SDK. Session shares the selected login/Region across clients.
        # The local profile lives in ~/.aws/config; EC2 later uses its own IAM role.
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        config = Config(
            connect_timeout=10, read_timeout=120,
            retries={"mode": "standard", "total_max_attempts": 3},
        )

        # The Free Tier API's documented endpoint is in us-east-1. This reads the project
        # plan only; deployed resources still use args.region (eu-north-1).
        plan = session.client("freetier", region_name="us-east-1", config=config).get_account_plan_state()
        # A paused/inactive project cannot be deployed to; this changes no billing settings.
        if plan["accountPlanStatus"] != "ACTIVE":
            raise ValueError("The AWS project plan is not active. Check AWS Settings > Billing.")
        print(f"AWS plan: {plan['accountPlanType']}. Deploying one server in {args.region}.", flush=True)

        ssm = session.client("ssm", config=config)
        cfn = session.client("cloudformation", config=config)
        s3 = session.client("s3", config=config)
        parameter = f"/financial-research/{args.stack}/runtime" # parameter store directory name
        template = (ROOT / "deployment/stack.yaml").read_text()
        # Validate the template before requesting any infrastructure changes.
        cfn.validate_template(TemplateBody=template)

        # Scratch directory on this computer, automatically removed when the block ends.
        with tempfile.TemporaryDirectory(prefix="financial-research-release-") as temporary:
            # 1. Prepare locally: create the archive from committed HEAD and reuse runtime settings.
            artifact = Path(temporary) / "release.tar.gz"
            revision, digest = bundle(ROOT, artifact)
            configure(ssm, parameter, replace=args.configure)

            # 2. Ensure infrastructure exists: CloudFormation supplies EC2 and the S3 bucket.
            print("Creating or updating the reviewed CloudFormation stack…", flush=True)
            # AWS maintains this public Parameter Store entry with the latest Amazon Linux
            image_id = ssm.get_parameter(
                Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
            )["Parameter"]["Value"]
            outputs = ensure_stack(cfn, args.stack, template, domain, image_id)
            instance, bucket = outputs["InstanceId"], outputs["ArtifactBucket"]
            print(f"Server IP: {outputs['PublicIp']}. Point {domain}'s A record to this address.", flush=True)

            # 3. Upload Mac -> S3: send the archive, then wait for EC2's SSM Agent to be ready.
            key = f"releases/{revision}/{digest}.tar.gz"
            s3.upload_file(str(artifact), bucket, key)
            wait_for_ssm(ssm, instance)

            # 4. Run on EC2 via SSM: download, verify, and extract the archive;
            #    then host.sh loads secrets and builds/starts the containers.
            response = ssm.send_command(
                InstanceIds=[instance], DocumentName="AWS-RunShellScript", TimeoutSeconds=600,
                Parameters={
                    "commands": [release_command(bucket, key, digest, revision, parameter, domain)],
                    "executionTimeout": ["1800"],
                },
            )
            command = response["Command"]["CommandId"]
            print(f"Building and starting the app through SSM (command {command})…", flush=True)

            # 5. Wait for completion: report success only after the remote script succeeds.
            ssm.get_waiter("command_executed").wait(
                CommandId=command, InstanceId=instance,
                WaiterConfig={"Delay": 5, "MaxAttempts": 360},
            )

        # The remote health check covers app startup; public HTTPS still needs a browser check.
        print(f"App health check passed. Once DNS resolves, open https://{domain} and use your shared login.")
        print("Then verify an offline demo and one bounded Bedrock research run in the browser.")
        return 0
    except (BotoCoreError, ClientError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
