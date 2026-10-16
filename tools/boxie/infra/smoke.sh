#!/usr/bin/env bash
# Run from the repository root after building the sandbox image and echor.
set -euo pipefail

docker pull busybox:1.37.0
smoke_dir=$(mktemp -d)
export SMOKE_DIR="$smoke_dir"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    cat "$smoke_dir/api.log" "$smoke_dir/orchestrator.log" 2>/dev/null || true
  fi
  kill "${api_pid:-}" "${orchestrator_pid:-}" 2>/dev/null || true
  wait "${api_pid:-}" "${orchestrator_pid:-}" 2>/dev/null || true
  rm -rf "$smoke_dir"
}
trap cleanup EXIT
uv run --locked python - <<'PYTHON'
import json, os, pathlib, secrets, subprocess
root = pathlib.Path(os.environ["SMOKE_DIR"])
for name in ("orchestrator", "consumer"):
    path = root / f"{name}.secret"
    path.write_text(secrets.token_hex(32))
    path.chmod(0o600)
header = root / "consumer.headers"
header.write_text("Authorization: Bearer " + (root / "consumer.secret").read_text())
header.chmod(0o600)
generic = json.loads(pathlib.Path("tools/boxie/infra/profiles.example.json").read_text())
product = json.loads(pathlib.Path("tools/cli-playground/infra/profiles/playground.json").read_text())
for profile, image in [(generic["smoke"], "busybox:1.37.0"), (product["playground"], "clis-playground-sandbox:smoke")]:
    profile["image_digest"] = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()
(root / "profiles.json").write_text(json.dumps({**generic, **product}))
PYTHON
uv run --locked python -m boxie_infra.config --output "$smoke_dir" \
  --data-dir "$smoke_dir/data" --installation-id "$(cat /proc/sys/kernel/random/uuid)" \
  --host-id ci --consumer-id smoke --orchestrator-url http://127.0.0.1:8091 --bind-host 127.0.0.1 \
  --profiles "$smoke_dir/profiles.json" --orchestrator-credential "$smoke_dir/orchestrator.secret" \
  --consumer-credential "$smoke_dir/consumer.secret"
.venv/bin/boxie-orchestrator --config "$smoke_dir/orchestrator.json" >"$smoke_dir/orchestrator.log" 2>&1 &
orchestrator_pid=$!
.venv/bin/boxie-api --config "$smoke_dir/api.json" >"$smoke_dir/api.log" 2>&1 &
api_pid=$!
for _attempt in $(seq 1 60); do
  if curl --fail --silent --header "@$smoke_dir/consumer.headers" http://127.0.0.1:8090/readyz >/dev/null; then break; fi
  if ! kill -0 "$orchestrator_pid" "$api_pid"; then cat "$smoke_dir/orchestrator.log" "$smoke_dir/api.log"; exit 1; fi
  sleep 1
done
curl --fail --silent --header "@$smoke_dir/consumer.headers" http://127.0.0.1:8090/readyz
uv run --locked python -m boxie_infra.smoke \
  --boxie-url http://127.0.0.1:8090 --credential-file "$smoke_dir/consumer.secret"
uv run --locked python -m playground_infra.smoke --binary "$PWD/target/release/echor" \
  --boxie-url http://127.0.0.1:8090 --credential-file "$smoke_dir/consumer.secret"
uv run --locked python - <<'PYTHON'
import json, os, pathlib
registry = json.loads((pathlib.Path(os.environ["SMOKE_DIR"]) / "data/registry.json").read_text())
assert len(registry["records"]) == 2
assert all(record["state"] in {"terminated", "failed"} and not record["cleanup_pending"] and not record["creation_uncertain"] for record in registry["records"])
print("PASS: no active reservations or uncertain cleanup remain")
PYTHON
