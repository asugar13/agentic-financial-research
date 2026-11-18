"""Check release packaging and AWS orchestration without provisioning anything."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

pytest.importorskip("bcrypt")
import bcrypt
boto3 = pytest.importorskip("boto3")
from botocore.exceptions import WaiterError
from botocore.stub import Stubber

from financial_research.diagnostics import RunRecorder
from financial_research.state import ResearchState

SPEC = importlib.util.spec_from_file_location("deploy", Path(__file__).parents[1] / "deployment/deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def client(service):
    # Explicit fake credentials prevent any credential-chain or metadata requests.
    return boto3.client(service, region_name="eu-north-1", aws_access_key_id="testing", aws_secret_access_key="testing")


def test_release_uses_committed_sources_and_excludes_notes_runs_and_credentials(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    git("init")
    for relative in (*deploy.RELEASE_PATHS, "docs/pass-1.md", ".aws/credentials", "runs/private/state.json"):
        path = tmp_path / relative
        if relative in {"src", ".streamlit", "deployment"}:
            path = path / "example.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("example")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", "commit", "-m", "Fixture release")
    archive = tmp_path / "release.tar.gz"
    revision, digest = deploy.bundle(tmp_path, archive)
    assert revision == git("rev-parse", "HEAD").stdout.strip()
    assert len(digest) == 64
    with tarfile.open(archive) as package:
        names = package.getnames()
    assert "Dockerfile" in names and "src/example.txt" in names
    assert not any(name.startswith(("docs/", ".aws/", "runs/", ".git/")) for name in names)
    (tmp_path / "Dockerfile").write_text("unreviewed edit")
    with pytest.raises(subprocess.CalledProcessError):
        deploy.bundle(tmp_path, archive)


def test_configure_hashes_password_before_sending_settings_to_aws(monkeypatch):
    ssm = client("ssm")
    prompts = iter(["test-only-password", "test-only-password", ""])
    monkeypatch.setattr("builtins.input", lambda _: "research")
    monkeypatch.setattr(deploy.getpass, "getpass", lambda _: next(prompts))
    sent = []
    monkeypatch.setattr(ssm, "put_parameter", lambda **kwargs: sent.append(kwargs))
    with Stubber(ssm) as stub:
        stub.add_client_error("get_parameter", service_error_code="ParameterNotFound",
                              expected_params={"Name": "/runtime", "WithDecryption": True})
        deploy.configure(ssm, "/runtime")
    assert sent[0]["Type"] == "SecureString"
    assert "test-only-password" not in sent[0]["Value"]
    settings = json.loads(sent[0]["Value"])
    assert bcrypt.checkpw(b"test-only-password", settings["password_hash"].encode())


def test_existing_settings_are_reused_without_prompting(monkeypatch):
    ssm = client("ssm")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Existing settings should be reused"))
    with Stubber(ssm) as stub:
        stub.add_response("get_parameter", {"Parameter": {"Type": "SecureString", "Value": "{}"}},
                          {"Name": "/runtime", "WithDecryption": True})
        deploy.configure(ssm, "/runtime")


def test_app_update_preserves_the_servers_existing_ami():
    cfn = client("cloudformation")
    base = {"StackName": "research", "TemplateBody": "template", "Capabilities": ["CAPABILITY_IAM"],
            "Tags": [{"Key": "Project", "Value": "FinancialResearch"}]}
    create = {**base, "Parameters": [{"ParameterKey": "DomainName", "ParameterValue": "research.duckdns.org"},
                                     {"ParameterKey": "ImageId", "ParameterValue": "ami-new"}]}
    update = {**base, "Parameters": [create["Parameters"][0], {"ParameterKey": "ImageId", "UsePreviousValue": True}]}
    with Stubber(cfn) as stub:
        stub.add_client_error("create_stack", service_error_code="AlreadyExistsException", expected_params=create)
        stub.add_client_error("update_stack", service_error_code="ValidationError",
                              service_message="No updates are to be performed.", expected_params=update)
        stub.add_response("describe_stacks", {"Stacks": [{"StackName": "research", "CreationTime": "2026-11-08T00:00:00Z",
                          "StackStatus": "CREATE_COMPLETE", "Outputs": [{"OutputKey": "PublicIp", "OutputValue": "192.0.2.1"}]}]},
                          {"StackName": "research"})
        assert deploy.ensure_stack(cfn, "research", "template", "research.duckdns.org", "ami-new") == {"PublicIp": "192.0.2.1"}


def test_failed_release_is_not_reported_as_a_success(monkeypatch, tmp_path, capsys):
    class SSM:
        def get_parameter(self, **kwargs):
            return {"Parameter": {"Value": "ami-test"}}

        def send_command(self, **kwargs):
            assert "password" not in kwargs["Parameters"]["commands"][0]
            return {"Command": {"CommandId": "command"}}

        def get_waiter(self, name):
            return self

        def wait(self, **kwargs):
            raise WaiterError(name="command_executed", reason="Failed", last_response={"Status": "Failed"})

    class OtherServices:
        def get_account_plan_state(self):
            return {"accountPlanStatus": "ACTIVE", "accountPlanType": "FREE"}

        def validate_template(self, **kwargs):
            pass

        def upload_file(self, *args):
            pass

    class Session:
        def client(self, name, **kwargs):
            return SSM() if name == "ssm" else OtherServices()

    monkeypatch.setattr(deploy.boto3, "Session", lambda **kwargs: Session())
    monkeypatch.setattr(deploy, "bundle", lambda *args: ("a" * 40, "b" * 64))
    monkeypatch.setattr(deploy, "configure", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, "wait_for_ssm", lambda *args: None)
    monkeypatch.setattr(deploy, "ensure_stack", lambda *args: {"InstanceId": "i-test", "ArtifactBucket": "private", "PublicIp": "192.0.2.1"})
    assert deploy.main(["--domain", "research.duckdns.org"]) == 1
    captured = capsys.readouterr()
    assert "Deployment failed" in captured.err
    assert "App health check passed" not in captured.out


def test_another_region_is_rejected_before_using_aws(monkeypatch):
    monkeypatch.setattr(deploy.boto3, "Session", lambda **kwargs: pytest.fail("AWS must not be contacted"))
    with pytest.raises(SystemExit) as exc:
        deploy.main(["--domain", "research.duckdns.org", "--region", "eu-west-1"])
    assert exc.value.code == 2


def test_container_revision_is_recorded_without_calling_git(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_REVISION", "a" * 40)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Container should not need git"))
    recorder = RunRecorder(tmp_path / "run", ResearchState(question="Test"), mode="demo", model="scripted", review=False)
    assert recorder.data["git_revision"] == "a" * 40
