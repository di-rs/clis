"""Preview selection and publishing boundaries with real manifests and archives."""

import io
import json
import subprocess
import sys
import time
import urllib.error
import zipfile
from collections.abc import Callable, Iterator
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


def test_build_script_runs_without_installed_workspace_packages(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(Path(preview_ci.__file__).resolve()),
            "build",
            "--base",
            "HEAD",
            "--head",
            "HEAD",
            "--output",
            str(tmp_path / "artifact"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "CLI playground builds: none\n"
    assert not (tmp_path / "artifact").exists()


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
        self.requests: list[str] = []
        self.payload = archive()
        self.stale_after_download = False
        self.deployments: list[dict[str, object]] = []
        self.workflow_runs: list[dict[str, object]] = []

    def request(
        self, path: str, *, method: str = "GET", body: object = None, limit: int = 2 * 1024 * 1024
    ) -> bytes:
        self.requests.append(path)
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
        elif path.startswith(f"repos/{REPO}/actions/workflows/cli-playground.yml/runs?"):
            assert f"head_sha={SHA}" in path
            assert "event=pull_request" in path
            response = {"workflow_runs": self.workflow_runs}
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


def test_pr_change_during_download_prevents_upload() -> None:
    github = FakeGitHub()
    github.stale_after_download = True
    gateway = Mock(side_effect=AssertionError("stale upload"))
    assert (
        preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
        is None
    )
    assert github.writes == []


def test_pr_closure_during_batch_upload_stops_github_writes() -> None:
    github = FakeGitHub()

    def gateway(*args: object, **kwargs: object) -> dict[str, object]:
        github.pull["state"] = "closed"
        return {"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32}

    assert (
        preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
        is None
    )
    assert github.writes == []


def test_batch_uses_one_upload_one_preview_and_constant_github_requests() -> None:
    github = FakeGitHub()
    github.payload = archive([f"cli{index:04}" for index in range(1024)])
    identifier = "a" * 32
    url = f"https://{identifier}.play.example/p/{identifier}"
    gateway = Mock(return_value={"id": identifier, "url": url})
    body = preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
    gateway.assert_called_once_with(
        "https://play.example",
        "token",
        f"/api/environments?sha={SHA}&pr=7&run_id=500&run_number=2&run_attempt=1",
        method="POST",
        data=github.payload,
    )
    assert len(github.requests) <= 15
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
def test_failed_batch_never_records_success_or_an_untrusted_link(failure: object) -> None:
    github = FakeGitHub()
    github.payload = archive(["echor", "sorty"])
    gateway = Mock(
        side_effect=[
            failure,
            {"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32},
        ]
    )
    with pytest.raises(ValueError, match="Failed to publish"):
        preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
    assert gateway.call_count == 2
    assert gateway.call_args_list[0].kwargs["method"] == "POST"
    assert gateway.call_args_list[1].kwargs["method"] == "PUT"
    assert not any("/deployments" in path for path, _ in github.writes)
    body = str(github.writes[-1][1])
    assert "Publish failed" in body and "evil.example" not in body


def test_stale_close_event_does_not_delete_a_reopened_preview() -> None:
    github = FakeGitHub()
    gateway = Mock(side_effect=AssertionError)
    assert (
        preview_ci.lifecycle(
            github, REPO, 7, "closed", "https://play.example", "token", gateway=gateway
        )
        is None
    )
    assert github.writes == []


def test_lifecycle_rejects_a_different_base_repository() -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    github.pull["base"] = {"ref": "master", "repo": {"full_name": "other/repo"}}
    gateway = Mock(side_effect=AssertionError)
    assert (
        preview_ci.lifecycle(
            github, REPO, 7, "closed", "https://play.example", "token", gateway=gateway
        )
        is None
    )
    assert github.writes == []


def test_closed_pull_is_deleted_and_only_its_deployment_becomes_inactive() -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    github.deployments = [
        {"id": 10, "environment": "cli-playground/pr-7"},
        {"id": 11, "environment": "cli-playground/pr-70"},
    ]
    gateway = Mock(return_value={"deleted": 1})
    assert preview_ci.lifecycle(
        github, REPO, 7, "closed", "https://play.example", "token", gateway=gateway
    )
    gateway.assert_called_once_with(
        "https://play.example", "token", "/api/pull-requests/7", method="DELETE"
    )
    assert github.writes == [
        (
            f"repos/{REPO}/deployments/10/statuses",
            {"state": "inactive", "description": "Pull request closed", "auto_inactive": False},
        )
    ]


def test_reopened_pull_clears_tombstone_without_reactivating_old_deployments() -> None:
    github = FakeGitHub()
    gateway = Mock(return_value={"reopened": 7})
    assert preview_ci.lifecycle(
        github, REPO, 7, "reopened", "https://play.example", "token", gateway=gateway
    )
    gateway.assert_called_once_with(
        "https://play.example", "token", "/api/pull-requests/7/reopen", method="POST"
    )
    assert github.writes == []


@pytest.mark.parametrize("status", ["in_progress", "completed"])
def test_reopen_replays_only_latest_current_head_build_after_clearing_tombstone(
    status: str,
) -> None:
    github = FakeGitHub()
    github.run["status"] = status
    github.workflow_runs = [
        {**github.run, "id": 499, "run_number": 1},
        github.run,
        {**github.run, "id": 501, "run_number": 3, "head_sha": "b" * 40},
        {**github.run, "id": 502, "run_number": 4, "path": ".github/workflows/other.yml"},
    ]
    requests: list[tuple[str, str]] = []

    def gateway(
        base: str, token: str, path: str, *, method: str, **kwargs: object
    ) -> dict[str, object]:
        requests.append((method, path))
        if path.endswith("/reopen"):
            return {"reopened": 7}
        return {"id": "a" * 32, "url": "https://pr-7.play.example/p/" + "a" * 32}

    preview_ci.lifecycle(
        github, REPO, 7, "reopened", "https://play.example", "secret", gateway=gateway
    )
    assert len(requests) == 2
    assert requests[0] == ("POST", "/api/pull-requests/7/reopen")
    if status == "in_progress":
        assert requests[1] == ("PUT", "/api/pull-requests/7/build")
    else:
        assert requests[1][0] == "POST" and requests[1][1].startswith("/api/environments?")
    assert github.writes[-1][0] == f"repos/{REPO}/issues/7/comments"


def test_large_deployment_history_does_not_fail_successful_hosted_cleanup() -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    # Every page is full, exhausting the real bounded GitHub paginator.
    github.deployments = [{"id": index, "environment": "production"} for index in range(100)]
    gateway = Mock(return_value={"deleted": 2})
    message = preview_ci.lifecycle(
        github, REPO, 7, "closed", "https://play.example", "token", gateway=gateway
    )
    assert message is not None and "Deleted 2 hosted" in message
    assert "pagination limit" in message and "may remain active" in message
    assert github.writes == []


def test_unconfigured_cleanup_does_not_claim_hosted_cleanup() -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    gateway = Mock(side_effect=AssertionError)
    message = preview_ci.lifecycle(github, REPO, 7, "closed", "", "", gateway=gateway)
    assert message is not None and "No hosted environments were cleaned" in message
    assert github.writes == []


def test_missing_cleanup_acknowledgement_prevents_github_updates() -> None:
    github = FakeGitHub()
    github.pull["state"] = "closed"
    gateway = Mock(return_value={"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32})
    with pytest.raises(ValueError):
        preview_ci.lifecycle(
            github, REPO, 7, "closed", "https://play.example", "token", gateway=gateway
        )
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
    response: bytes, *, status: int | Callable[[], int] = 200, delay: float = 0
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
            self.send_response(status() if callable(status) else status)
            self.send_header("Location", "/stolen")
            self.end_headers()
            try:
                self.wfile.write(response)
            except BrokenPipeError, ConnectionResetError:
                pass

        do_DELETE = do_POST
        do_PUT = do_POST
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


def gateway_at(origin: str) -> preview_ci.GatewayRequest:
    def request(
        base: str,
        token: str,
        path: str,
        *,
        method: str,
        data: bytes | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, object]:
        assert base == "https://play.example"
        return preview_ci.gateway_request(
            origin, token, path, method=method, data=data, content_type=content_type
        )

    return request


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
        assert (
            preview_ci.upload(
                FakeGitHub(),
                REPO,
                base,
                "secret",
                SHA,
                7,
                bundle,
                run_id=500,
                run_number=2,
                run_attempt=1,
            )
            == result
        )
    assert received == [
        (
            "POST",
            f"/api/environments?sha={SHA}&pr=7&run_id=500&run_number=2&run_attempt=1",
            "Bearer secret",
            bundle,
        )
    ]


@pytest.mark.parametrize(
    ("status", "conclusion", "artifacts_present", "expected"),
    [
        ("queued", None, False, "building"),
        ("in_progress", None, False, "building"),
        ("completed", "failure", True, "failed"),
        ("completed", "cancelled", True, "cancelled"),
        ("completed", "success", False, "empty"),
    ],
)
def test_publish_records_current_build_state_without_allocating_a_sandbox(
    status: str,
    conclusion: str | None,
    artifacts_present: bool,
    expected: str,
) -> None:
    github = FakeGitHub()
    github.run.update(status=status, conclusion=conclusion)
    if not artifacts_present:
        github.artifacts.clear()
    url = "https://pr-7.play.example/p/" + "a" * 32
    response = {"id": "a" * 32, "url": url}
    with gateway_server(json.dumps(response).encode()) as (base, received):
        gateway = gateway_at(base)
        body = preview_ci.publish(
            github, REPO, 500, "https://play.example", "secret", gateway=gateway
        )
    assert body is not None
    assert url in body
    assert len(received) == 1
    method, path, authorization, payload = received[0]
    assert (method, path, authorization) == ("PUT", "/api/pull-requests/7/build", "Bearer secret")
    assert json.loads(payload) == {
        "sha": SHA,
        "run_id": 500,
        "run_number": 2,
        "run_attempt": 1,
        "state": expected,
    }


def test_reopened_successful_build_with_expired_artifact_is_unavailable_not_empty() -> None:
    github = FakeGitHub()
    github.artifacts[0]["expired"] = True
    github.workflow_runs = [github.run]
    response = {
        "reopened": 7,
        "id": "a" * 32,
        "url": "https://pr-7.play.example/p/" + "a" * 32,
    }
    with gateway_server(json.dumps(response).encode()) as (base, received):
        gateway = gateway_at(base)
        preview_ci.lifecycle(
            github, REPO, 7, "reopened", "https://play.example", "secret", gateway=gateway
        )
    assert [(method, path) for method, path, _, _ in received] == [
        ("POST", "/api/pull-requests/7/reopen"),
        ("PUT", "/api/pull-requests/7/build"),
    ]
    assert json.loads(received[1][3])["state"] == "failed"
    assert len(github.writes) == 1
    comment = str(github.writes[0][1])
    assert "artifacts expired; rerun build" in comment.lower()
    assert "No CLI binary artifacts were produced" not in comment


def test_preview_url_accepts_only_its_pr_subdomain() -> None:
    identifier = "a" * 32
    url = f"https://pr-7.play.example/p/{identifier}"
    assert preview_ci.preview_url("https://play.example", {"id": identifier, "url": url}, 7) == url
    with pytest.raises(ValueError, match="invalid preview URL"):
        preview_ci.preview_url("https://play.example", {"id": identifier, "url": url}, 70)


def test_ready_preview_is_not_relabelled_by_a_late_building_event() -> None:
    github = FakeGitHub()
    github.run.update(status="in_progress", conclusion=None)
    gateway = Mock(
        return_value={
            "id": "a" * 32,
            "url": "https://pr-7.play.example/p/" + "a" * 32,
            "build": {
                "sha": SHA,
                "run_id": 500,
                "run_number": 2,
                "run_attempt": 1,
                "state": "ready",
            },
        }
    )
    assert (
        preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
        is None
    )
    assert github.writes == []


def test_upload_recovers_after_the_gateway_is_busy_for_over_a_minute() -> None:
    delays: list[float] = []
    bundle = archive(["echor", "catr"])
    result = {"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32}
    with gateway_server(
        json.dumps(result).encode(), status=lambda: 429 if sum(delays) < 65 else 200
    ) as (base, received):
        assert (
            preview_ci.upload(
                FakeGitHub(),
                REPO,
                base,
                "secret",
                SHA,
                7,
                bundle,
                run_id=500,
                run_number=2,
                run_attempt=1,
                sleep=delays.append,
            )
            == result
        )
    assert 65 <= sum(delays) <= 120
    assert 1 < len(received) <= 8
    assert all(
        request
        == (
            "POST",
            f"/api/environments?sha={SHA}&pr=7&run_id=500&run_number=2&run_attempt=1",
            "Bearer secret",
            bundle,
        )
        for request in received
    )


def test_pr_head_change_during_backoff_stops_upload_and_github_writes() -> None:
    github = FakeGitHub()
    waits = 0

    def sleep(seconds: float) -> None:
        nonlocal waits
        waits += 1
        if waits == 2:
            github.pull["head"] = {"sha": "b" * 40}

    result = {"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32}
    with gateway_server(
        json.dumps(result).encode(), status=lambda: 429 if len(received) <= 2 else 200
    ) as (base, received):
        gateway = gateway_at(base)
        assert (
            preview_ci.publish(
                github, REPO, 500, "https://play.example", "token", gateway=gateway, sleep=sleep
            )
            is None
        )
    assert len(received) == 2
    assert waits == 2
    assert github.writes == []


@pytest.mark.parametrize("status", [429, 401])
def test_upload_preserves_http_failure_after_its_retry_budget(status: int) -> None:
    delays: list[float] = []

    def sleep(seconds: float) -> None:
        delays.append(seconds)
        assert sum(delays) <= 120

    with gateway_server(b"rejected", status=status) as (base, received):
        with pytest.raises(urllib.error.HTTPError) as error:
            preview_ci.upload(
                FakeGitHub(),
                REPO,
                base,
                "secret",
                SHA,
                7,
                archive(),
                run_id=500,
                run_number=2,
                run_attempt=1,
                sleep=sleep,
            )
        assert error.value.code == status
    assert sum(delays) == (120 if status == 429 else 0)
    assert 1 < len(received) <= 8 if status == 429 else len(received) == 1


def test_gateway_redirects_never_forward_credentials() -> None:
    with gateway_server(b"redirect", status=307) as (base, received):
        with pytest.raises((ValueError, urllib.error.HTTPError)):
            preview_ci.gateway_request(base, "secret", "/api/pull-requests/7", method="POST")
    assert received == [("POST", "/api/pull-requests/7", "Bearer secret", b"")]


@pytest.mark.parametrize("status", ["in_progress", "completed"])
def test_server_rejecting_a_stale_run_prevents_comments_and_deployment_updates(
    status: str,
) -> None:
    github = FakeGitHub()
    github.run["status"] = status
    with gateway_server(b'{"detail":"stale build"}', status=409) as (base, received):
        gateway = gateway_at(base)
        assert (
            preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
            is None
        )
    assert len(received) == 1
    assert github.writes == []


@pytest.mark.parametrize(
    "detail", ["build identity conflict", "this revision already has a different binary"]
)
def test_upload_does_not_hide_genuine_server_conflicts(detail: str) -> None:
    with gateway_server(json.dumps({"detail": detail}).encode(), status=409) as (base, received):
        with pytest.raises(urllib.error.HTTPError) as error:
            preview_ci.upload(
                FakeGitHub(),
                REPO,
                base,
                "secret",
                SHA,
                7,
                archive(),
                run_id=500,
                run_number=2,
                run_attempt=1,
            )
        assert error.value.code == 409
    assert len(received) == 1


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
def test_invalid_artifacts_are_never_uploaded(artifact: str) -> None:
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
    gateway = Mock(return_value={"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32})
    with pytest.raises(ValueError, match="Failed to publish"):
        preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
    assert gateway.call_count == 1
    assert gateway.call_args.kwargs["method"] == "PUT"
    assert "Publish failed" in str(github.writes[-1][1])
    assert not any("/deployments" in path for path, _ in github.writes)


def test_only_the_exact_bundle_artifact_name_reaches_upload() -> None:
    github = FakeGitHub()
    github.artifacts[0]["name"] = "cli-playground-binaries-extra"
    gateway = Mock(return_value={"id": "a" * 32, "url": "https://play.example/p/" + "a" * 32})
    body = preview_ci.publish(github, REPO, 500, "https://play.example", "token", gateway=gateway)
    assert gateway.call_count == 1
    assert gateway.call_args.kwargs["method"] == "PUT"
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
