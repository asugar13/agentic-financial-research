#!/usr/bin/env bash
# Called through SSM after the committed release has been downloaded and verified.
set -euo pipefail
# New settings files must be readable only by their owner (root on EC2).
umask 077
export AWS_DEFAULT_REGION=eu-north-1

# deploy.py passes these three positional arguments through its SSM release command.
# Full Parameter Store name of the runtime SecureString containing the login and API keys.
parameter=$1

# Public hostname for Caddy, without https:// (e.g. agentic-financial-research.duckdns.org).
domain=$2

# Git commit ID of the downloaded release; baked into the image for saved run diagnostics.
revision=$3

# Bedrock model ID for the hosted app; this is a fixed setting, not a script argument.
model=qwen.qwen3-32b-v1:0

# SSM may start in another directory; resolve the downloaded release from this script.
release_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Keep reports and HTTPS certificates outside releases so container updates preserve them.
state_dir=/var/lib/financial-research
image="financial-research:$(basename "$release_dir")"

# Serialize releases so two operators cannot replace each other's containers.
exec 9>/var/lock/financial-research-deploy.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }

mkdir -p "$state_dir/runs" "$state_dir/caddy-data" "$state_dir/caddy-config"
# Match the non-root app user's UID from Dockerfile so it can save research runs.
chown 10001:10001 "$state_dir/runs"

# Only the parameter name enters SSM command logs. Its decrypted value stays on disk.
aws ssm get-parameter --name "$parameter" --with-decryption \
    --query Parameter.Value --output text > "$state_dir/settings.json"

# Convert the downloaded settings into Docker env files before starting containers.
python3 - "$state_dir" "$domain" "$model" <<'PY'
import json
from pathlib import Path
import re
import sys

directory, domain, model = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = json.loads((directory / "settings.json").read_text())
username, password_hash = settings["username"], settings["password_hash"]
fred_key = settings.get("fred_api_key", "")
exa_key = settings.get("exa_api_key", "")

# The proxy needs a safe username and a bcrypt hash, not a plaintext password.
if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", username):
    raise ValueError("Invalid login username.")
if not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", password_hash):
    raise ValueError("Expected a bcrypt password hash.")

if fred_key and not re.fullmatch(r"[A-Fa-f0-9]{32}", fred_key):
    raise ValueError("Expected a 32-character FRED key.")

# Reject line breaks/NULs that could corrupt Docker's environment-file format.
for value in (domain, model, exa_key):
    if any(character in value for character in "\r\n\x00"):
        raise ValueError("Invalid environment value.")

# Keep provider keys in the app environment and the login hash in the proxy environment.
# RESEARCH_HOSTED=1 gives the UI Bedrock/demo options instead of offering local Ollama.
(directory / "app.env").write_text(
    f"AWS_REGION=eu-north-1\nBEDROCK_MODEL={model}\nRESEARCH_HOSTED=1\n"
    f"FRED_API_KEY={fred_key}\nEXA_API_KEY={exa_key}\n"
)
(directory / "proxy.env").write_text(
    f"APP_DOMAIN={domain}\nAPP_USERNAME={username}\nAPP_PASSWORD_HASH={password_hash}\n"
)

# The env files now contain the settings; remove the extra decrypted JSON copy.
(directory / "settings.json").unlink()
PY

# The shared Docker network lets Caddy reach the app by its container name.
docker network inspect financial-research >/dev/null 2>&1 || docker network create financial-research

# Build the downloaded source on EC2. The revision is recorded in saved run diagnostics.
docker build --pull --build-arg "APP_REVISION=$revision" --tag "$image" "$release_dir"
docker pull caddy:2-alpine

# Validate the new proxy configuration while the current app/proxy are still running.
docker run --rm --env-file "$state_dir/proxy.env" \
    --volume "$release_dir/deployment/Caddyfile:/etc/caddy/Caddyfile:ro" \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile

# Test a candidate before replacing the running app. Run files live outside the image.
docker rm -f financial-research-next >/dev/null 2>&1 || true
# Clean up any candidate left behind if this script exits before the rename below.
trap 'docker rm -f financial-research-next >/dev/null 2>&1 || true' EXIT

# The image is read-only except for run storage and temporary caches.
# Rotate container logs to limit their disk use.
docker run -d --name financial-research-next --restart unless-stopped \
    --network financial-research --env-file "$state_dir/app.env" \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,noexec,size=128m \
    --tmpfs /home/research:rw,nosuid,noexec,uid=10001,gid=10001,size=64m \
    --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
    --volume "$state_dir/runs:/app/runs" "$image"

# Wait for the API to serve the React page before replacing the live app. This does not test data providers.
healthy=false
for attempt in $(seq 1 45); do
    if [ "$(docker inspect --format '{{.State.Health.Status}}' financial-research-next)" = healthy ]; then
        healthy=true
        break
    fi
    sleep 2
done

if [ "$healthy" != true ]; then
    echo 'Candidate failed its health check; the previous app remains running.' >&2
    exit 1
fi

# Caddy forwards to this stable container name. Replacement causes a brief interruption.
docker rm -f financial-research-app >/dev/null 2>&1 || true
docker rename financial-research-next financial-research-app

# Recreate the proxy with the new config; mounted certificate data survives the replacement.
cp "$release_dir/deployment/Caddyfile" "$state_dir/Caddyfile"
docker rm -f financial-research-proxy >/dev/null 2>&1 || true
docker run -d --name financial-research-proxy --restart unless-stopped \
    --network financial-research --env-file "$state_dir/proxy.env" \
    --publish 80:80 --publish 443:443 \
    --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
    --volume "$state_dir/Caddyfile:/etc/caddy/Caddyfile:ro" \
    --volume "$state_dir/caddy-data:/data" --volume "$state_dir/caddy-config:/config" \
    caddy:2-alpine

# Catch an immediate proxy crash; public DNS, TLS, and login still need separate checks.
sleep 2
if [ "$(docker inspect --format '{{.State.Running}}' financial-research-proxy)" != true ]; then
    echo 'HTTPS proxy stopped; inspect docker logs financial-research-proxy.' >&2
    exit 1
fi

# Record source paths for a manual rollback; this does not trigger an automatic rollback.
# current-release is a text file containing a folder path. If it exists (-f),
# cat reads that path; only save it as previous-release when it differs from the
# new release_dir. Redeploying the same release therefore preserves the older rollback path.
if [ -f "$state_dir/current-release" ] && [ "$(cat "$state_dir/current-release")" != "$release_dir" ]; then
    cp "$state_dir/current-release" "$state_dir/previous-release"
fi
printf '%s\n' "$release_dir" > "$state_dir/current-release"
echo 'App health check passed. HTTPS becomes available once the hostname resolves to this server.'
