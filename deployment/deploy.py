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
RELEASE_PATHS = ("Dockerfile", ".dockerignore", "pyproject.toml", "requirements.lock", "src", ".streamlit", "deployment")


def bundle(root: Path, destination: Path) -> tuple[str, str]:
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *RELEASE_PATHS], cwd=root, check=True, capture_output=True)
    with destination.open("wb") as output:
        subprocess.run(["git", "archive", "--format=tar.gz", revision, "--", *RELEASE_PATHS], cwd=root, check=True, stdout=output)
    return revision, hashlib.sha256(destination.read_bytes()).hexdigest()


def configure(ssm, name: str, *, replace: bool = False) -> None:
    try:
        existing = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
    except ssm.exceptions.ParameterNotFound:
        existing = None
    if existing and not replace:
        if existing["Type"] != "SecureString":
            raise ValueError("The runtime settings parameter must be a SecureString.")
        return

    username = input("Shared app username [research]: ").strip() or "research"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", username):
        raise ValueError("Use 1–32 letters, digits, dots, underscores, or hyphens for the username.")
    password = getpass.getpass("Shared app password (12–72 bytes): ")
    if not 12 <= len(password.encode()) <= 72:
        raise ValueError("Use a password containing 12–72 UTF-8 bytes.")
    if password != getpass.getpass("Repeat app password: "):
        raise ValueError("Passwords did not match.")
    fred_key = getpass.getpass("FRED API key (optional; Enter to skip): ").strip()
    if fred_key and not re.fullmatch(r"[A-Fa-f0-9]{32}", fred_key):
        raise ValueError("Expected a 32-character FRED API key.")
    settings = {"username": username, "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                "fred_api_key": fred_key}
    # The plaintext password is never sent to AWS; FRED settings remain encrypted.
    ssm.put_parameter(Name=name, Type="SecureString", Value=json.dumps(settings), Overwrite=True)


def ensure_stack(cfn, name: str, template: str, domain: str, image_id: str) -> dict[str, str]:
    options = {"StackName": name, "TemplateBody": template, "Capabilities": ["CAPABILITY_IAM"],
               "Parameters": [{"ParameterKey": "DomainName", "ParameterValue": domain},
                              {"ParameterKey": "ImageId", "ParameterValue": image_id}],
               "Tags": [{"Key": "Project", "Value": "FinancialResearch"}]}
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
            if exc.response["Error"].get("Code") != "ValidationError" or "No updates are to be performed" not in exc.response["Error"].get("Message", ""):
                raise
            waiter = None
    if waiter:
        cfn.get_waiter(waiter).wait(StackName=name, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    return {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}


def wait_for_ssm(ssm, instance: str) -> None:
    for _ in range(90):
        info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance]}])
        if any(item["PingStatus"] == "Online" for item in info["InstanceInformationList"]):
            return
        time.sleep(5)
    raise TimeoutError("The instance did not register with Systems Manager; inspect its bootstrap logs.")


def release_command(bucket: str, key: str, digest: str, revision: str, parameter: str, domain: str) -> str:
    directory = f"/opt/financial-research/releases/{digest}"
    q = shlex.quote
    return "\n".join([
        "set -eu", "umask 077", f"export AWS_DEFAULT_REGION={REGION}", "cloud-init status --wait",
        f"mkdir -p {q(directory)}",
        f"aws s3 cp {q(f's3://{bucket}/{key}')} {q(directory + '/release.tar.gz')}",
        f"cd {q(directory)}",
        f"printf '%s\\n' {q(digest + '  release.tar.gz')} | sha256sum --check --status",
        "tar --extract --gzip --file release.tar.gz --no-same-owner --no-same-permissions",
        f"bash deployment/host.sh {q(parameter)} {q(domain)} {q(revision)}",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, help="Your hostname, e.g. your-name.duckdns.org")
    parser.add_argument("--stack", default="financial-research")
    parser.add_argument("--profile", default="financial-research")
    parser.add_argument("--region", choices=[REGION], default=REGION)
    parser.add_argument("--configure", action="store_true", help="Replace the stored app login and FRED settings")
    args = parser.parse_args(argv)
    domain = args.domain.lower()
    if len(domain) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        parser.error("Enter a hostname without a scheme, port, or path.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", args.stack):
        parser.error("Use a CloudFormation stack name beginning with a letter and containing letters, digits, or hyphens.")

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        config = Config(connect_timeout=10, read_timeout=120, retries={"mode": "standard", "total_max_attempts": 3})
        plan = session.client("freetier", region_name="us-east-1", config=config).get_account_plan_state()
        if plan["accountPlanStatus"] != "ACTIVE":
            raise ValueError("The AWS project plan is not active. Check AWS Settings > Billing.")
        print(f"AWS plan: {plan['accountPlanType']}. Deploying one server in {args.region}.", flush=True)
        ssm = session.client("ssm", config=config)
        cfn = session.client("cloudformation", config=config)
        s3 = session.client("s3", config=config)
        parameter = f"/financial-research/{args.stack}/runtime"
        template = (ROOT / "deployment/stack.yaml").read_text()
        cfn.validate_template(TemplateBody=template)
        with tempfile.TemporaryDirectory(prefix="financial-research-release-") as temporary:
            artifact = Path(temporary) / "release.tar.gz"
            revision, digest = bundle(ROOT, artifact)
            configure(ssm, parameter, replace=args.configure)
            print("Creating or updating the reviewed CloudFormation stack…", flush=True)
            image_id = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64")["Parameter"]["Value"]
            outputs = ensure_stack(cfn, args.stack, template, domain, image_id)
            instance, bucket = outputs["InstanceId"], outputs["ArtifactBucket"]
            print(f"Server IP: {outputs['PublicIp']}. Point {domain}'s A record to this address.", flush=True)
            key = f"releases/{revision}/{digest}.tar.gz"
            s3.upload_file(str(artifact), bucket, key)
            wait_for_ssm(ssm, instance)
            response = ssm.send_command(
                InstanceIds=[instance], DocumentName="AWS-RunShellScript", TimeoutSeconds=600,
                Parameters={"commands": [release_command(bucket, key, digest, revision, parameter, domain)],
                            "executionTimeout": ["1800"]},
            )
            command = response["Command"]["CommandId"]
            print(f"Building and starting the app through SSM (command {command})…", flush=True)
            ssm.get_waiter("command_executed").wait(CommandId=command, InstanceId=instance,
                                                   WaiterConfig={"Delay": 5, "MaxAttempts": 360})
        print(f"App health check passed. Once DNS resolves, open https://{domain} and use your shared login.")
        print("Then verify an offline demo and one bounded Bedrock research run in the browser.")
        return 0
    except (BotoCoreError, ClientError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
