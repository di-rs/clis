"""Preview selection and publishing boundaries with real manifests and archives."""

import io
import json
import subprocess
import sys
import time
import urllib.error
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import preview_ci
import pytest
from ci.github import GitHub
from playground import artifacts

from .helpers import elf_binary

SHA = "a" * 40
REPO = "di-rs/clis"
RUN = f"repos/{REPO}/actions/runs/500"
PR = f"repos/{REPO}/pulls/7"


def archive(
    names: str | list[str] = "echor",
    *,
    mode: int = 0o100755,
    payload: bytes = elf_binary(),
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name in [names] if isinstance(names, str) else names:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(entry, payload)
    return output.getvalue()


def workspace(root: Path) -> dict[str, str]:
    (root / "Cargo.toml").write_text('[workspace]\nmembers = ["coreutils/*", "utils/*"]\n')
    for name, directory in (("echor", "coreutils/echor"), ("catr", "coreutils/catr")):
        target = root / directory
        (target / "src").mkdir(parents=True)
        (target / "Cargo.toml").write_text(f'[package]\nname = "{name}"\n')
        (target / "src/main.rs").write_text("fn main() {}")
    library = root / "utils/shared"
    library.mkdir(parents=True)
    (library / "Cargo.toml").write_text('[package]\nname = "shared"\n')
    return {"catr": "coreutils/catr", "echor": "coreutils/echor"}


def test_discovery_excludes_libraries_and_selection_uses_owning_packages(tmp_path: Path) -> None:
    packages = workspace(tmp_path)
    assert preview_ci.discover(tmp_path) == packages
    assert preview_ci.select(packages, ["coreutils/echor/src/main.rs"]) == ["echor"]
    assert (
        preview_ci.select(packages, ["coreutils/echor/README.md", "coreutils/catr/tests/a.rs"])
        == []
    )


@pytest.mark.parametrize("path", ["Cargo.lock", "utils/shared/src/lib.rs"])
def test_shared_changes_select_all_packages(path: str) -> None:
    assert preview_ci.select({"echor": "coreutils/echor", "catr": "coreutils/catr"}, [path]) == [
        "catr",
        "echor",
    ]


def test_discovery_rejects_an_additional_binary(tmp_path: Path) -> None:
    workspace(tmp_path)
    extra = tmp_path / "coreutils/echor/src/bin"
    extra.mkdir()
    (extra / "other.rs").write_text("fn main() {}")
    with pytest.raises(ValueError):
        preview_ci.discover(tmp_path)


def test_compressed_bundle_expansion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    data = archive(["echor", "catr"], payload=elf_binary(bytes(1024)))
    assert len(data) < 512
    monkeypatch.setattr(artifacts, "MAX_ARCHIVE", 512)
    with pytest.raises(ValueError, match="bundle exceeds limits"):
        artifacts.binary_archive(data)


class FakeGitHub(GitHub):
    def __init__(self) -> None:
        self.pull: dict[str, object] = {
            "number": 7,
            "state": "open",
            "head": {"sha": SHA},
            "base": {"ref": "master", "repo": {"full_name": REPO}},
        }
        self.run: dict[str, object] = {
            "id": 500,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/cli-playground.yml",
            "name": "CLI playground build",
            "workflow_id": 21,
            "repository": {"full_name": REPO},
            "head_sha": SHA,
            "run_number": 2,
            "run_attempt": 1,
            "pull_requests": [{"number": 7}],
        }
        self.artifacts: list[dict[str, object]] = [
            {
                "id": 81,
                "name": "cli-playground-binaries",
                "size_in_bytes": 200,
                "expired": False,
                "workflow_run": {"id": 500, "head_sha": SHA},
            }
        ]
        self.writes: list[tuple[str, object]] = []
        self.payload = archive()
        self.stale_after_download = False
        self.deployments: list[dict[str, object]] = []

    def request(
        self, path: str, *, method: str = "GET", body: object = None, limit: int = 2 * 1024 * 1024
    ) -> bytes:
        if method != "GET":
            self.writes.append((path, body))
            return json.dumps({"id": 99}).encode()
        response: object
        if path == RUN:
            response = self.run
        elif path == f"repos/{REPO}/actions/workflows/cli-playground.yml":
            response = {"id": 21, "path": ".github/workflows/cli-playground.yml"}
        elif path == PR:
            response = self.pull
        elif path.startswith(RUN + "/artifacts?"):
            response = {"artifacts": self.artifacts}
        elif path.startswith(f"repos/{REPO}/issues/7/comments?"):
            response = []
        elif path.startswith(f"repos/{REPO}/deployments?"):
            response = self.deployments
        elif path == f"repos/{REPO}/actions/artifacts/81/zip":
            if self.stale_after_download:
                self.pull["head"] = {"sha": "b" * 40}
            return self.payload
        else:
            raise AssertionError(path)
        return json.dumps(response).encode()


def test_unconfigured_publish_reports_artifact_links_without_fake_live_url() -> None:
    github = FakeGitHub()
    body = preview_ci.publish(github, REPO, 500, "", "")
    assert body is not None
    assert "Configuration needed" in body
    assert "https://github.com/di-rs/clis/actions/runs/500/artifacts/81" in body
    assert "/p/" not in body
    assert len(github.writes) == 1


@pytest.mark.parametrize("change", ["closed", "head", "workflow", "repo"])
def test_stale_or_unrelated_run_has_no_writes(change: str) -> None:
    github = FakeGitHub()
    if change == "closed":
        github.pull["state"] = "closed"
    elif change == "head":
        github.pull["head"] = {"sha": "b" * 40}
    elif change == "workflow":
        github.run["workflow_id"] = 99
    else:
        github.run["repository"] = {"full_name": "attacker/repo"}
    assert preview_ci.publish(github, REPO, 500, "", "") is None
    assert github.writes == []


def test_pr_change_during_download_prevents_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    github = FakeGitHub()
    github.stale_after_download = True
    monkeypatch.setattr(preview_ci, "upload", Mock(side_effect=AssertionError("stale upload")))
    assert preview_ci.publish(github, REPO, 500, "https://play.example", "token") is None
    assert github.writes == []


def test_pr_closure_during_batch_upload_stops_github_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()

    def uploaded(*args: object) -> dict[str, str]:
        github.pull["state"] = "closed"
        return {"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32}

    monkeypatch.setattr(preview_ci, "upload", uploaded)
    assert preview_ci.publish(github, REPO, 500, "https://play.example", "token") is None
    assert github.writes == []


def test_batch_uses_one_upload_one_preview_and_constant_github_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    github.payload = archive([f"cli{index:04}" for index in range(1024)])
    identifier = "a" * 32
    url = f"https://{identifier}.play.example/p/{identifier}"
    uploaded = Mock(return_value={"id": identifier, "url": url})
    requests = Mock(wraps=github.request)
    monkeypatch.setattr(github, "request", requests)
    monkeypatch.setattr(preview_ci, "upload", uploaded)
    body = preview_ci.publish(github, REPO, 500, "https://play.example", "token")
    uploaded.assert_called_once_with("https://play.example", "token", SHA, 7, github.payload)
    assert requests.call_count <= 15
    assert body is not None and body.count(url) == 1 and len(body) < 1000
    assert "| CLI |" not in body
    deployments = [value for path, value in github.writes if path.endswith("/deployments")]
    assert len(deployments) == 1
    assert isinstance(deployments[0], dict)
    assert deployments[0]["environment"] == "cli-playground/pr-7"
    statuses = [value for path, value in github.writes if path.endswith("/statuses")]
    assert len(statuses) == 1 and isinstance(statuses[0], dict)
    assert statuses[0]["environment_url"] == url


@pytest.mark.parametrize(
    "failure", [ValueError("upload failed"), {"id": "a" * 32, "url": "https://evil.example"}]
)
def test_failed_batch_never_records_success_or_an_untrusted_link(
    monkeypatch: pytest.MonkeyPatch, failure: object
) -> None:
    github = FakeGitHub()
    github.payload = archive(["echor", "sorty"])
    uploaded = (
        Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    )
    monkeypatch.setattr(preview_ci, "upload", uploaded)
    with pytest.raises(ValueError, match="Failed to publish"):
        preview_ci.publish(github, REPO, 500, "https://play.example", "token")
    assert uploaded.call_count == 1
    assert not any("/deployments" in path for path, _ in github.writes)
    body = str(github.writes[-1][1])
    assert "Publish failed" in body and "evil.example" not in body


def test_stale_close_event_does_not_delete_a_reopened_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    monkeypatch.setattr(preview_ci, "gateway_request", Mock(side_effect=AssertionError))
    assert preview_ci.lifecycle(github, REPO, 7, "closed", "https://play.example", "token") is None
    assert github.writes == []


def test_lifecycle_rejects_a_different_base_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    github.pull["base"] = {"ref": "master", "repo": {"full_name": "other/repo"}}
    monkeypatch.setattr(preview_ci, "gateway_request", Mock(side_effect=AssertionError))
    assert preview_ci.lifecycle(github, REPO, 7, "closed", "https://play.example", "token") is None
    assert github.writes == []


def test_closed_pull_is_deleted_and_only_its_deployment_becomes_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    github.deployments = [
        {"id": 10, "environment": "cli-playground/pr-7"},
        {"id": 11, "environment": "cli-playground/pr-70"},
    ]
    gateway = Mock(return_value={"deleted": 1})
    monkeypatch.setattr(preview_ci, "gateway_request", gateway)
    assert preview_ci.lifecycle(github, REPO, 7, "closed", "https://play.example", "token")
    gateway.assert_called_once_with(
        "https://play.example", "token", "/api/pull-requests/7", method="DELETE"
    )
    assert github.writes == [
        (
            f"repos/{REPO}/deployments/10/statuses",
            {"state": "inactive", "description": "Pull request closed", "auto_inactive": False},
        )
    ]


def test_reopened_pull_clears_tombstone_without_reactivating_old_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    gateway = Mock(return_value={"reopened": 7})
    monkeypatch.setattr(preview_ci, "gateway_request", gateway)
    assert preview_ci.lifecycle(github, REPO, 7, "reopened", "https://play.example", "token")
    gateway.assert_called_once_with(
        "https://play.example", "token", "/api/pull-requests/7/reopen", method="POST"
    )
    assert github.writes == []


def test_large_deployment_history_does_not_fail_successful_hosted_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    # Every page is full, exhausting the real bounded GitHub paginator.
    github.deployments = [{"id": index, "environment": "production"} for index in range(100)]
    monkeypatch.setattr(preview_ci, "gateway_request", lambda *args, **kwargs: {"deleted": 2})
    message = preview_ci.lifecycle(github, REPO, 7, "closed", "https://play.example", "token")
    assert message is not None and "Deleted 2 hosted" in message
    assert "pagination limit" in message and "may remain active" in message
    assert github.writes == []


def test_unconfigured_cleanup_does_not_claim_hosted_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    monkeypatch.setattr(preview_ci, "gateway_request", Mock(side_effect=AssertionError))
    message = preview_ci.lifecycle(github, REPO, 7, "closed", "", "")
    assert message is not None and "No hosted environments were cleaned" in message
    assert github.writes == []


def test_missing_cleanup_acknowledgement_prevents_github_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    monkeypatch.setattr(preview_ci, "gateway_request", lambda *args, **kwargs: {})
    with pytest.raises(ValueError):
        preview_ci.lifecycle(github, REPO, 7, "closed", "https://play.example", "token")
    assert github.writes == []


def test_lifecycle_failure_does_not_log_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "do-not-log-this-secret"

    def failed(*args: object) -> str:
        raise ValueError(secret)

    monkeypatch.setattr(preview_ci, "lifecycle", failed)
    monkeypatch.setattr(
        sys,
        "argv",
        ["preview_ci.py", "lifecycle", "--repo", REPO, "--pr", "7", "--action", "closed"],
    )
    with pytest.raises(SystemExit) as stopped:
        preview_ci.main()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err + str(stopped.value)


@contextmanager
def gateway_server(
    response: bytes, *, status: int = 200, delay: float = 0
) -> Iterator[tuple[str, list[tuple[str, str, str | None, bytes]]]]:
    received: list[tuple[str, str, str | None, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(
                (
                    self.command,
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))),
                )
            )
            time.sleep(delay)
            self.send_response(status)
            self.send_header("Location", "/stolen")
            self.end_headers()
            try:
                self.wfile.write(response)
            except BrokenPipeError, ConnectionResetError:
                pass

        do_DELETE = do_POST
        do_GET = do_POST

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", received
        finally:
            server.shutdown()
            thread.join(timeout=1)


def test_lifecycle_gateway_transport_authenticates_deletion() -> None:
    with gateway_server(b'{"deleted": 1}') as (base, received):
        assert preview_ci.gateway_request(
            base, "secret", "/api/pull-requests/7", method="DELETE"
        ) == {"deleted": 1}
    assert received == [("DELETE", "/api/pull-requests/7", "Bearer secret", b"")]


def test_upload_sends_the_entire_zip_in_one_request() -> None:
    bundle = archive(["echor", "catr"])
    result = {"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32}
    with gateway_server(json.dumps(result).encode()) as (base, received):
        assert preview_ci.upload(base, "secret", SHA, 7, bundle) == result
    assert received == [("POST", f"/api/environments?sha={SHA}&pr=7", "Bearer secret", bundle)]


def test_gateway_redirects_never_forward_credentials() -> None:
    with gateway_server(b"redirect", status=307) as (base, received):
        with pytest.raises((ValueError, urllib.error.HTTPError)):
            preview_ci.gateway_request(base, "secret", "/api/pull-requests/7", method="POST")
    assert received == [("POST", "/api/pull-requests/7", "Bearer secret", b"")]


def test_gateway_response_size_is_bounded() -> None:
    with gateway_server(b" " * 16385) as (base, _):
        with pytest.raises(ValueError, match="response exceeds size limit"):
            preview_ci.gateway_request(base, "secret", "/api/pull-requests/7", method="DELETE")


def test_gateway_stalled_response_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preview_ci, "TIMEOUT", 0.05)
    with gateway_server(b'{"deleted": 2}', delay=0.3) as (base, _):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            preview_ci.gateway_request(base, "secret", "/api/pull-requests/7", method="DELETE")
        assert time.monotonic() - started < 0.25


def test_frontend_changes_do_not_build_rust_binaries() -> None:
    assert (
        preview_ci.select(
            {"echor": "coreutils/echor"}, ["tools/cli-playground/frontend/src/App.svelte"]
        )
        == []
    )


@pytest.mark.parametrize(
    "artifact", ["earlier_attempt", "other_run", "unsafe_member", "symlink", "bad_elf"]
)
def test_invalid_artifacts_are_never_uploaded(
    monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    github = FakeGitHub()
    if artifact == "earlier_attempt":
        github.run.update({"run_attempt": 2, "run_started_at": "2026-10-13T12:00:00Z"})
        github.artifacts[0]["created_at"] = "2026-10-13T11:00:00Z"
    elif artifact == "other_run":
        github.artifacts[0]["workflow_run"] = {"id": 501, "head_sha": SHA}
    elif artifact == "unsafe_member":
        github.payload = archive(["echor", "../sorty"])
    elif artifact == "symlink":
        github.payload = archive(mode=0o120755)
    else:
        github.payload = archive(payload=b"not ELF" * 10)
    monkeypatch.setattr(preview_ci, "upload", Mock(side_effect=AssertionError("invalid upload")))
    with pytest.raises(ValueError, match="Failed to publish"):
        preview_ci.publish(github, REPO, 500, "https://play.example", "token")
    assert "Publish failed" in str(github.writes[-1][1])
    assert not any("/deployments" in path for path, _ in github.writes)


def test_only_the_exact_bundle_artifact_name_reaches_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeGitHub()
    github.artifacts[0]["name"] = "cli-playground-binaries-extra"
    monkeypatch.setattr(preview_ci, "upload", Mock(side_effect=AssertionError("unexpected upload")))
    body = preview_ci.publish(github, REPO, 500, "https://play.example", "token")
    assert body is not None and "No CLI binary artifacts" in body


def test_configured_origin_requires_https() -> None:
    with pytest.raises(ValueError):
        preview_ci.public_origin("http://play.example")


@pytest.mark.parametrize("count", [0, 100])
def test_build_command_invokes_cargo_once_for_the_selected_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    names = [f"cli{index:03}" for index in range(count)]
    for name in [*names, "unused"]:
        executable = tmp_path / "target/release" / name
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(elf_binary(name.encode()))
        executable.chmod(0o755)
    paths = [f"coreutils/{name}/src/main.rs" for name in reversed(names)]
    paths += ["coreutils/unused/README.md"]
    commands = Mock(return_value=subprocess.CompletedProcess([], 0, "\0".join(paths).encode()))
    monkeypatch.setattr(subprocess, "run", commands)
    monkeypatch.setattr(preview_ci, "ROOT", tmp_path)
    monkeypatch.setattr(
        preview_ci, "discover", lambda: {name: f"coreutils/{name}" for name in [*names, "unused"]}
    )
    output = tmp_path / "artifact"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        sys,
        "argv",
        ["preview_ci.py", "build", "--base", "base", "--head", "head", "--output", str(output)],
    )
    preview_ci.main()
    assert commands.call_args_list[0].args[0] == [
        "git",
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        "base...head",
        "--",
    ]
    assert summary.read_text() == "CLI playground builds: " + (", ".join(names) or "none") + "\n"
    if not names:
        assert commands.call_count == 1
        assert not list(output.glob("*"))
        return
    assert commands.call_count == 2
    commands.assert_called_with(
        [
            "cargo",
            "build",
            "--locked",
            "--release",
            "--bins",
            *(arg for name in names for arg in ("--package", name)),
        ],
        cwd=tmp_path,
        check=True,
    )
    assert sorted(path.name for path in output.iterdir()) == names
    for name in names:
        assert (output / name).read_bytes() == elf_binary(name.encode())
        assert (output / name).stat().st_mode & 0o111
