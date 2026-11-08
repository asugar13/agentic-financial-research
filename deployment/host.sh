#!/usr/bin/env bash
# Called through SSM after the committed release has been downloaded and verified.
set -euo pipefail
umask 077
export AWS_DEFAULT_REGION=eu-north-1

parameter=$1
domain=$2
revision=$3
model=qwen.qwen3-32b-v1:0
release_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state_dir=/var/lib/financial-research
image="financial-research:$(basename "$release_dir")"

# Serialize releases so two operators cannot replace each other's containers.
exec 9>/var/lock/financial-research-deploy.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
mkdir -p "$state_dir/runs" "$state_dir/caddy-data" "$state_dir/caddy-config"
chown 10001:10001 "$state_dir/runs"

# Only the parameter name enters SSM command logs. Its decrypted value stays on disk.
aws ssm get-parameter --name "$parameter" --with-decryption \
    --query Parameter.Value --output text > "$state_dir/settings.json"
python3 - "$state_dir" "$domain" "$model" <<'PY'
import json
from pathlib import Path
import re
import sys

directory, domain, model = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = json.loads((directory / "settings.json").read_text())
username, password_hash = settings["username"], settings["password_hash"]
fred_key = settings.get("fred_api_key", "")
if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", username):
    raise ValueError("Invalid login username.")
if not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", password_hash):
    raise ValueError("Expected a bcrypt password hash.")
if fred_key and not re.fullmatch(r"[A-Fa-f0-9]{32}", fred_key):
    raise ValueError("Expected a 32-character FRED key.")
for value in (domain, model):
    if any(character in value for character in "\r\n\x00"):
        raise ValueError("Invalid environment value.")
(directory / "app.env").write_text(
    f"AWS_REGION=eu-north-1\nBEDROCK_MODEL={model}\nRESEARCH_HOSTED=1\nFRED_API_KEY={fred_key}\n"
)
(directory / "proxy.env").write_text(
    f"APP_DOMAIN={domain}\nAPP_USERNAME={username}\nAPP_PASSWORD_HASH={password_hash}\n"
)
(directory / "settings.json").unlink()
PY

docker network inspect financial-research >/dev/null 2>&1 || docker network create financial-research
docker build --pull --build-arg "APP_REVISION=$revision" --tag "$image" "$release_dir"
docker pull caddy:2-alpine
docker run --rm --env-file "$state_dir/proxy.env" \
    --volume "$release_dir/deployment/Caddyfile:/etc/caddy/Caddyfile:ro" \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile

# Test a candidate before replacing the running app. Run files live outside the image.
docker rm -f financial-research-next >/dev/null 2>&1 || true
trap 'docker rm -f financial-research-next >/dev/null 2>&1 || true' EXIT
docker run -d --name financial-research-next --restart unless-stopped \
    --network financial-research --env-file "$state_dir/app.env" \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,noexec,size=128m \
    --tmpfs /home/research:rw,nosuid,noexec,uid=10001,gid=10001,size=64m \
    --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
    --volume "$state_dir/runs:/app/runs" "$image"
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

docker rm -f financial-research-app >/dev/null 2>&1 || true
docker rename financial-research-next financial-research-app
cp "$release_dir/deployment/Caddyfile" "$state_dir/Caddyfile"
docker rm -f financial-research-proxy >/dev/null 2>&1 || true
docker run -d --name financial-research-proxy --restart unless-stopped \
    --network financial-research --env-file "$state_dir/proxy.env" \
    --publish 80:80 --publish 443:443 \
    --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
    --volume "$state_dir/Caddyfile:/etc/caddy/Caddyfile:ro" \
    --volume "$state_dir/caddy-data:/data" --volume "$state_dir/caddy-config:/config" \
    caddy:2-alpine
sleep 2
if [ "$(docker inspect --format '{{.State.Running}}' financial-research-proxy)" != true ]; then
    echo 'HTTPS proxy stopped; inspect docker logs financial-research-proxy.' >&2
    exit 1
fi
if [ -f "$state_dir/current-release" ] && [ "$(cat "$state_dir/current-release")" != "$release_dir" ]; then
    cp "$state_dir/current-release" "$state_dir/previous-release"
fi
printf '%s\n' "$release_dir" > "$state_dir/current-release"
echo 'App health check passed. HTTPS becomes available once the hostname resolves to this server.'
