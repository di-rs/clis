"""Production-manifest adapters for the existing native lifecycle fixture.

Only loopback endpoints, local-registry images and fixture owner/project names
are adapted. Host disk bootstrap and public DNS/ACME/Access are activation gates.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from string import Template
from typing import Any

import yaml
from boxie_infra.component import storage_requests
from boxie_infra.profiles import main as profile_operation
from deploy_tool.manifests import source_manifest
from deploy_tool.prepare import canonical, load_prepared, prepare
from deploy_tool.remote import Activator, read_bundle
from host_infra.contracts import HostHandle
from host_infra.resources import _directory
from run_integration import ROOT

from integration.stack import LifecycleStack


class DeploymentStack(LifecycleStack):
    def __init__(self, root: Path, *, monitoring: bool) -> None:
        super().__init__(root)
        self.monitoring = monitoring
        self.runtime_services = ("api", "orchestrator")
        self.component = "assembled-boxie-" + self.installation.replace("-", "")[:12]
        self.product_component = self.component.replace("boxie", "playground")
        self.runtime = "clis-" + self.component
        self.release_root = root / "releases"
        self.resources: list[tuple[str, str]] = []
        self.image_tags: list[str] = []
        self.bundles: dict[str, dict[str, Any]] = {}
        self.images: dict[str, str] = {}
        self.registry = self.name + "-registry"
        self.preflight_passed = False
        self.registry_address = ""
        self.registry_started = False
        self.monitoring_started = False
        self.release_ids: set[str] = set()
        self.counter = 0
        self.env["BOXIE_API_ALIAS"] = "boxie-api"
        self.env["BOXIE_DATA_DIR"] = str(root / "boxie")
        self.env["BOXIE_MONITORING"] = str(monitoring).lower()
        self.env["OBS_METRICS_NETWORK"] = self.name + "-metrics"
        self.env["OBS_GRAFANA_ORIGIN_NETWORK"] = self.name + "-grafana-origin"
        self.env["OBS_TEMPORAL_UI_ORIGIN_NETWORK"] = self.name + "-temporal-origin"

    def resource(self, kind: str, name: str, *options: str) -> None:
        self.command(
            [
                "docker",
                kind,
                "create",
                "--label",
                "boxie.integration=" + self.name,
                "--label",
                "clis.installation=" + self.installation,
                "--label",
                "clis.owner=platform",
                *options,
                name,
            ]
        )
        self.resources.append((kind, name))

    def start(self) -> None:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeError(
                "assembled deployment requires a disposable Linux amd64 host, root for real UID ownership, configured runsc and built lifecycle images; no fallback"
            )
        info = json.loads(self.command(["docker", "info", "--format", "{{json .}} "]))
        if info["Architecture"] not in ("x86_64", "amd64") or "runsc" not in info["Runtimes"]:
            raise RuntimeError("assembled deployment requires Linux amd64/runsc; no fallback")
        if not self.binary.is_file():
            raise RuntimeError("build the native echor binary before selecting deployment")
        self.preflight_passed = True
        volumes, directories = storage_requests(
            HostHandle(self.installation, self.host, str(self.root)), self.monitoring
        )
        for entry in (*volumes, *directories):
            _directory(self.root, entry, create=True)
        for volume in volumes:
            self.resource(
                "volume",
                volume.name,
                "--driver",
                "local",
                "--opt",
                "type=none",
                "--opt",
                "o=bind",
                "--opt",
                "device=" + str(self.root / volume.directory),
            )
            mapping = {
                "postgres": "POSTGRES_VOLUME",
                "vector": "OBS_VECTOR_VOLUME",
                "logs": "OBS_LOGS_VOLUME",
                "prometheus": "OBS_PROMETHEUS_VOLUME",
                "grafana": "OBS_GRAFANA_VOLUME",
            }
            self.env[mapping[Path(volume.directory).name]] = volume.name
        for name in (
            self.env["BOXIE_CONSUMER_NETWORK"],
            *(
                [
                    self.env[k]
                    for k in (
                        "OBS_METRICS_NETWORK",
                        "OBS_GRAFANA_ORIGIN_NETWORK",
                        "OBS_TEMPORAL_UI_ORIGIN_NETWORK",
                    )
                ]
                if self.monitoring
                else []
            ),
        ):
            self.resource("network", name, "--internal")
        self.command(["docker", "pull", "registry:3"])
        registry_image = self.command(
            ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", "registry:3"]
        )
        self.command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.registry,
                "--label",
                "boxie.integration=" + self.name,
                "--publish",
                "127.0.0.1::5000",
                registry_image,
            ]
        )
        self.registry_started = True
        self.registry_address = (
            "localhost:" + self.command(["docker", "port", self.registry, "5000"]).rsplit(":", 1)[1]
        )
        end = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen("http://" + self.registry_address + "/v2/", timeout=1):
                    break
            except OSError:
                if time.monotonic() >= end:
                    raise RuntimeError("disposable registry did not become ready") from None
                time.sleep(0.1)
        for unit, local in (
            ("boxie", self.env["BOXIE_RUNTIME_IMAGE"]),
            ("playground", self.env["PLAYGROUND_IMAGE"]),
        ):
            self.images[unit] = self.pin_local(unit, local)
        super().start()
        self.evidence.update(
            source_revision=self.git(ROOT, "rev-parse", "HEAD"),
            production_manifests=True,
            monitoring=self.monitoring,
            registry_image=registry_image,
            adaptations=[
                "random owned projects/resources",
                "disposable bind-backed storage without provider disk bootstrap",
                "loopback-only test endpoints",
                "local registry pins",
                "DNS/ACME disabled in product proxy fixture",
            ],
        )
        (self.root / "evidence.json").write_bytes(canonical(self.evidence))

    def pin_local(self, unit: str, local: str) -> str:
        tag = self.registry_address + "/" + unit + ":fixture"
        self.command(["docker", "tag", local, tag])
        self.image_tags.append(tag)
        self.command(["docker", "push", tag])
        digests = json.loads(
            self.command(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", tag])
        )
        return next(
            str(value)
            for value in digests
            if str(value).startswith(self.registry_address + "/" + unit + "@")
        )

    @staticmethod
    def git(repository: Path, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.check_output(
            ["git", "-C", str(repository), *args], env=env, text=True, stderr=subprocess.DEVNULL
        ).strip()

    def compose(self, package: str, *args: str) -> str:
        owner = ROOT / "tools/boxie/infra" / package
        command = [
            "docker",
            "compose",
            "-p",
            getattr(self, package),
            "-f",
            str(owner / "compose.yml"),
        ]
        if (owner / "compose.production.yml").exists():
            command += ["-f", str(owner / "compose.production.yml")]
        if package == "temporal" and self.monitoring:
            command += ["-f", str(owner / "compose.observability.yml")]
        return self.command(command + ["-f", str(self.root / f"{package}-override.yml"), *args])

    def private_readers(self) -> None:
        for path in (
            self.root / "boxie",
            *self.root.glob("*.json"),
            *self.root.glob("*.secret"),
            *self.root.glob("*dsn"),
            *(self.root / "clients").glob("*.key"),
        ):
            os.chown(path, 10001, 10001)
            if path.is_file():
                path.chmod(0o600)
        for path in (self.root / "boxie").rglob("*"):
            os.chown(path, 10001, 10001)
        approved = Path(self.env["BOXIE_PROFILES_DIR"])
        os.chown(approved, 0, 10001)
        approved.chmod(0o750)
        (approved / "profiles.json").chmod(0o644)
        self.env["BOXIE_DOCKER_GID"] = str(Path("/var/run/docker.sock").stat().st_gid)

    def release(self, unit: str, *, failed: bool = False) -> dict[str, str]:
        self.counter += 1
        source = self.root / f"source-{unit}-{self.counter}"
        source.mkdir()
        owner = ROOT / "tools" / ("boxie" if unit == "boxie" else "cli-playground") / "infra"
        descriptor = json.loads((owner / "release-component.json").read_bytes())
        descriptor["component"] = self.component if unit == "boxie" else self.product_component
        for name in {descriptor["compose_file"], *descriptor["files"]}:
            shutil.copyfile(owner / name, source / name)
        config = yaml.safe_load((source / descriptor["compose_file"]).read_text())
        for service in config["services"].values():
            if "healthcheck" in service:
                service["healthcheck"].update(interval="1s", start_period="0s")
        settings = {
            key: value
            for key, value in self.env.items()
            if key in descriptor["required_environment"]
        }
        settings["FIXTURE_CREDENTIAL_VERSION"] = f"v{self.counter}"
        if unit == "boxie":
            settings.update(BOXIE_MONITORING=self.env["BOXIE_MONITORING"])
            if self.monitoring:
                settings["OBS_METRICS_NETWORK"] = self.env["OBS_METRICS_NETWORK"]
            config["services"]["api"]["ports"] = [
                "127.0.0.1:" + self.env["BOXIE_TEST_API_PORT"] + ":8090"
            ]
            images = {"BOXIE_RUNTIME_IMAGE": self.images["boxie"]}
            private: dict[str, object] = {}
        else:
            settings["PLAYGROUND_PUBLIC_URL"] = self.product_url
            config["services"]["gateway"]["environment"]["PLAYGROUND_PUBLIC_URL"] = (
                "${PLAYGROUND_PUBLIC_URL}"
            )
            config["services"]["gateway"]["environment"]["PLAYGROUND_PREVIEW_DOMAIN"] = ""
            config["services"]["gateway"]["ports"] = [
                "127.0.0.1:" + self.env["BOXIE_TEST_PRODUCT_PORT"] + ":8000"
            ]
            config["services"]["proxy"]["ports"] = ["127.0.0.1::80", "127.0.0.1::443"]
            config["services"]["proxy"]["command"] = [
                v
                for v in config["services"]["proxy"]["command"]
                if not v.startswith("--certificatesresolvers.")
            ]
            images = json.loads((owner / "release.example.json").read_bytes())["images"]
            images["PLAYGROUND_IMAGE"] = self.images["playground"]
            private = {
                "CLOUDFLARE_DNS_API_TOKEN": "fixture-dns-disabled",
                "PLAYGROUND_DEPLOY_TOKEN": self.deploy,
            }
        if failed:
            config["services"]["gateway"]["healthcheck"]["test"] = [
                "CMD",
                "python",
                "-c",
                "raise SystemExit(1)",
            ]
        (source / descriptor["compose_file"]).write_text(yaml.safe_dump(config, sort_keys=False))
        (source / "release-component.json").write_text(json.dumps(descriptor))
        self.git(source, "init", "-q")
        self.git(source, "add", ".")
        self.git(
            source,
            "-c",
            "user.name=Assembled fixture",
            "-c",
            "user.email=test@example.com",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "fixture",
        )
        revision = self.git(source, "rev-parse", "HEAD")
        manifest = source_manifest(
            source,
            "release-component.json",
            revision,
            dict[str, object](images),
            dict[str, object](settings),
        )
        output = self.root / f"prepared-{self.counter}"
        prepare(manifest, private, output)
        bundle = load_prepared(output)
        self.release_ids.add((output / "release-id").read_text())
        executable = ROOT / "tools/deploy/deploy_tool/remote.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(executable),
                "--root",
                str(self.release_root),
                "--timeout",
                "600",
                "--health-timeout",
                "5" if failed else "120",
            ],
            input=canonical(bundle),
            capture_output=True,
            timeout=620,
        )
        result: dict[str, str] = json.loads(completed.stdout)
        if not failed and (completed.returncode or result["outcome"] != "activated"):
            raise RuntimeError("assembled application release failed: " + result["outcome"])
        if not failed:
            self.bundles[unit] = bundle
        return result

    def application_command(self, unit: str, *args: str) -> str:
        bundle = self.bundles[unit]
        component = self.component if unit == "boxie" else self.product_component
        directory = (self.release_root / component / "current").resolve()
        retained = read_bundle(directory)
        adapter = Activator(self.release_root, 120, 60)
        return self.command(
            adapter.compose(retained, directory) + list(args),
            env=adapter.context(bundle, directory),
        )

    def runtime_command(self, *args: str) -> str:
        if "boxie" not in self.bundles and args and args[0] == "up":
            self.private_readers()
            if self.monitoring:
                self.start_monitoring()
            self.release("boxie")
            return ""
        return self.application_command("boxie", *args)

    def add_playground(self) -> None:
        content = json.loads((Path(self.env["BOXIE_PROFILES_DIR"]) / "profiles.json").read_bytes())
        product = json.loads(
            (ROOT / "tools/cli-playground/infra/profiles/playground.json").read_bytes()
        )
        product["playground"]["image_digest"] = self.command(
            [
                "docker",
                "image",
                "inspect",
                self.env.get("BOXIE_SANDBOX_IMAGE", "clis-playground-sandbox:lifecycle"),
                "--format",
                "{{.Id}}",
            ]
        )
        content.update(product)
        candidate = self.root / "candidate-profiles.json"
        candidate.write_text(json.dumps(content))
        container = self.runtime_command("ps", "-q", "orchestrator")
        assert (
            profile_operation(
                [
                    "--directory",
                    self.env["BOXIE_PROFILES_DIR"],
                    "--content",
                    str(candidate),
                    "--installation-id",
                    self.installation,
                    "--host-id",
                    self.host,
                    "--container",
                    container,
                    "--image",
                    self.images["boxie"],
                    "--profile",
                    "playground",
                    "--command",
                    '["/opt/playground/shell.sh"]',
                    "--project",
                    self.runtime,
                    "--release-root",
                    str(self.release_root),
                ],
                quiet=True,
            )
            == 0
        )
        for key, suffix, uid in (
            ("PLAYGROUND_DATA_VOLUME", "product", 10001),
            ("PLAYGROUND_CERTIFICATES_VOLUME", "certificates", 0),
        ):
            path = self.root / suffix
            path.mkdir(mode=0o700)
            os.chown(path, uid, uid)
            name = self.name + "-" + suffix
            self.resource(
                "volume",
                name,
                "--driver",
                "local",
                "--opt",
                "type=none",
                "--opt",
                "o=bind",
                "--opt",
                "device=" + str(path),
            )
            self.env[key] = name
        self.env.update(
            PLAYGROUND_BOXIE_URL="http://boxie-api:8090",
            PLAYGROUND_BOXIE_CREDENTIAL_FILE=self.env["BOXIE_CONSUMER_CREDENTIAL_FILE"],
            PLAYGROUND_BOXIE_PROFILE="playground",
            PLAYGROUND_DOMAIN="fixture.invalid",
            ACME_EMAIL="fixture@example.invalid",
            PLAYGROUND_CONSUMER_NETWORK=self.env["BOXIE_CONSUMER_NETWORK"],
            PLAYGROUND_PROXY_NETWORK=self.name + "-public",
        )
        self.resource("network", self.env["PLAYGROUND_PROXY_NETWORK"])
        self.release("playground")

    def start_monitoring(self) -> None:
        owner = ROOT / "tools/boxie/infra/observability"
        collector = self.name + ":collector"
        self.command(["docker", "build", "-t", collector, str(owner / "collector")])
        self.image_tags.append(collector)
        self.env["OBS_COLLECTOR_IMAGE"] = self.pin_local("collector", collector)
        pins = json.loads((owner / "versions.json").read_bytes())
        pin = pins["plugins"]["victoriametrics-logs-datasource"]
        with urllib.request.urlopen(pin["url"], timeout=30) as source:
            archive = source.read(64 * 1024 * 1024 + 1)
        if hashlib.sha256(archive).hexdigest() != pin["sha256"]:
            raise RuntimeError("pinned Grafana plugin integrity failure")
        plugins = self.root / "plugins"
        plugins.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            zipped.extractall(plugins)
            for member in zipped.infolist():
                path = plugins / member.filename
                path.chmod(
                    0o755 if path.is_dir() or (member.external_attr >> 16) & 0o111 else 0o644
                )
        prometheus = self.root / "prometheus.yml"
        prometheus.write_text(
            Template((owner / "prometheus.yml").read_text()).substitute(
                BOXIE_INSTALLATION_ID=self.installation,
                BOXIE_HOST_ID=self.host,
                BOXIE_TEMPORAL_NAMESPACE="boxie",
            )
        )
        password = self.root / "grafana.secret"
        password.write_text("fixture-" + self.installation)
        os.chown(password, 472, 0)
        password.chmod(0o600)
        self.env.update(
            OBS_PROMETHEUS_CONFIG_FILE=str(prometheus),
            OBS_GRAFANA_PLUGIN_DIR=str(plugins),
            OBS_GRAFANA_ADMIN_PASSWORD_FILE=str(password),
            OBS_GRAFANA_HOSTNAME="fixture.invalid",
            OBS_JOURNAL_GROUP_GID=self.command(["getent", "group", "systemd-journal"]).split(":")[
                2
            ],
            BOXIE_ENVIRONMENT="assembled",
            BOXIE_INSTALLATION_ID=self.installation,
            BOXIE_HOST_ID=self.host,
        )
        self.monitoring_started = True
        self.monitoring_command(
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "vector",
            "victorialogs",
            "prometheus",
            "grafana",
            "node-exporter",
        )

    def monitoring_command(self, *args: str) -> str:
        owner = ROOT / "tools/boxie/infra/observability"
        return self.command(
            [
                "docker",
                "compose",
                "-p",
                self.name + "-monitoring",
                "-f",
                str(owner / "compose.yml"),
                "-f",
                str(owner / "compose.production.yml"),
                *args,
            ]
        )

    def clean_applications(self) -> None:
        for project, services in (
            (self.runtime, {"api", "orchestrator"}),
            ("clis-" + self.product_component, {"gateway", "proxy"}),
        ):
            for container in self.command(
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]
            ).split():
                info = json.loads(self.command(["docker", "inspect", container]))[0]
                labels = info["Config"]["Labels"]
                if (
                    labels.get("com.docker.compose.project") != project
                    or labels.get("com.docker.compose.service") not in services
                    or labels.get("clis.release-id") not in self.release_ids
                ):
                    raise RuntimeError("refusing foreign assembled container cleanup")
                self.command(["docker", "rm", "-f", container])

    def close(self) -> None:
        if not self.preflight_passed:
            return
        errors = []
        for operation in (
            *(
                [lambda: self.application_command("playground", "down", "--remove-orphans")]
                if "playground" in self.bundles
                else []
            ),
            *(
                [lambda: self.application_command("boxie", "down", "--remove-orphans")]
                if "boxie" in self.bundles
                else []
            ),
            *(
                [lambda: self.monitoring_command("down", "--remove-orphans")]
                if self.monitoring_started
                else []
            ),
            self.clean_applications,
            self.clean_children,
        ):
            try:
                operation()
            except Exception as error:
                errors.append(error)
        self.runtime_started = False
        try:
            super().close()
        except Exception as error:
            errors.append(error)
        for kind, name in reversed(self.resources):
            try:
                info = json.loads(self.command(["docker", kind, "inspect", name]))[0]
                if info.get("Labels", {}).get("boxie.integration") != self.name:
                    raise RuntimeError("refusing foreign deployment resource cleanup")
                self.command(["docker", kind, "rm", name])
            except Exception as error:
                errors.append(error)
        if self.registry_started:
            try:
                info = json.loads(self.command(["docker", "inspect", self.registry]))[0]
                if info["Config"]["Labels"].get("boxie.integration") != self.name:
                    raise RuntimeError("refusing foreign registry cleanup")
                self.command(["docker", "rm", "-fv", self.registry])
            except Exception as error:
                errors.append(error)
        for tag in reversed(self.image_tags):
            try:
                self.command(["docker", "image", "rm", tag])
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("assembled fixture cleanup failed", errors)
