"""Production deployment boundaries, followed by real mTLS checks under integration."""

import asyncio
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
import yaml
from google.protobuf.duration_pb2 import Duration
from run_integration import ProductionStack, command
from temporal_support import tls
from temporalio import activity, workflow
from temporalio.api.namespace.v1 import NamespaceConfig
from temporalio.api.workflowservice.v1 import UpdateNamespaceRequest
from temporalio.client import Client
from temporalio.service import TLSConfig
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

ROOT = Path(__file__).parents[3]


@pytest.mark.integration
def test_postgres_requires_operator_supplied_control_network(tmp_path: Path) -> None:
    stack = ProductionStack(tmp_path)
    stack.write_overrides()
    try:
        with pytest.raises(
            RuntimeError, match="network.*(not found|could not be found|does not exist)"
        ):
            stack.compose("postgres", "up", "--detach")
    finally:
        stack.close()


@pytest.mark.integration
def test_postgres_preserves_operator_owned_control_network(tmp_path: Path) -> None:
    stack = ProductionStack(tmp_path)
    stack.write_overrides()
    network = stack.env["BOXIE_CONTROL_NETWORK"]
    command(["docker", "network", "create", "--internal", network])
    try:
        stack.compose("postgres", "up", "--detach", "--wait", "--wait-timeout", "90")
        stack.compose("postgres", "down", "--volumes", "--remove-orphans")
        info = json.loads(command(["docker", "network", "inspect", network]))
        assert info[0]["Internal"] is True
    finally:
        try:
            stack.close()
        finally:
            command(["docker", "network", "rm", network])


def test_private_production_deployment_has_explicit_jobs_and_trust() -> None:
    postgres_path = ROOT / "packages/boxie/infra/postgres/compose.yml"
    assert postgres_path.exists(), "Boxie PostgreSQL deployment missing"
    postgres = yaml.safe_load(postgres_path.read_text())
    temporal = yaml.safe_load((ROOT / "packages/boxie/infra/temporal/compose.yml").read_text())
    versions = json.loads((ROOT / "packages/boxie/infra/temporal/versions.json").read_text())
    for package in (postgres, temporal):
        for name, service in package["services"].items():
            assert not service.get("ports"), "production ports must remain private"
            assert "build" not in service, "operation jobs reuse existing runtime images"
            if package is temporal and name == "bootstrap":
                assert service["image"].startswith("${BOXIE_IMAGE:?")
                assert service["entrypoint"] == ["python", "-m", "boxie.bootstrap.temporal"]
                assert service["user"] == "1000:1000"
            else:
                assert "@sha256:" in service["image"]
            assert not any("docker.sock" in str(mount) for mount in service.get("volumes", []))
    assert temporal["services"]["temporal"]["image"] == versions["server"]["image"]
    assert temporal["services"]["schema"]["profiles"] == ["operations"]
    assert temporal["services"]["schema"]["image"] == versions["admin_tools"]["image"]
    assert temporal["services"]["bootstrap"]["profiles"] == ["operations"]
    config = yaml.safe_load(
        re.sub(
            r"{{.*?}}",
            "test-value",
            (ROOT / "packages/boxie/infra/temporal/config.yaml").read_text(),
        )
    )
    tls = config["global"]["tls"]
    for plane in ("frontend", "internode"):
        assert tls[plane]["server"]["requireClientAuth"] is True
        assert tls[plane]["server"]["clientCaFiles"]
        assert tls[plane]["client"]["disableHostVerification"] is False
    assert config["services"]["frontend"]["rpc"]["httpPort"] == 0
    assert config["global"]["authorization"] == {"authorizer": "", "claimMapper": ""}
    assert (
        temporal["services"]["ui"]["environment"]["TEMPORAL_TLS_ENABLE_HOST_VERIFICATION"] == "true"
    )


@activity.defn
async def deployment_activity(value: str) -> str:
    return "persisted:" + value


@workflow.defn
class DeploymentWorkflow:
    @workflow.run
    async def run(self, value: str) -> str:
        return await workflow.execute_activity(
            deployment_activity, value, start_to_close_timeout=timedelta(seconds=10)
        )


@pytest.mark.integration
def test_production_mtls_workflow_ui_and_database_isolation(temporal_stack: Any) -> None:
    async def exercise() -> None:
        stack = temporal_stack
        for identity in ("boxie-api", "boxie-supervisor", "operator", "ui-client"):
            client = await asyncio.wait_for(
                Client.connect(stack.address, namespace="boxie", tls=tls(stack, identity)), 10
            )
            assert await client.service_client.check_health()
        rejections: dict[str, str] = {}
        invalid_clients = {
            "no_certificate": TLSConfig(
                server_root_ca_cert=(stack.root / "clients/frontend-ca.pem").read_bytes(),
                domain="temporal",
            ),
            "foreign_ca": tls(stack, "untrusted"),
            "wrong_hostname": tls(stack, "boxie-api", domain="wrong-hostname"),
        }
        for name, invalid in invalid_clients.items():
            with pytest.raises((RuntimeError, TimeoutError)) as rejected:
                await asyncio.wait_for(
                    Client.connect(stack.address, namespace="boxie", tls=invalid), 6
                )
            rejections[name] = type(rejected.value).__name__ + ": " + str(rejected.value)[:500]
        stack.evidence["client_rejections"] = rejections
        client = await Client.connect(
            stack.address, namespace="boxie", tls=tls(stack, "boxie-supervisor")
        )
        queue = "deployment-" + uuid4().hex
        async with Worker(
            client,
            task_queue=queue,
            workflows=[DeploymentWorkflow],
            activities=[deployment_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            assert (
                await client.execute_workflow(
                    DeploymentWorkflow.run,
                    "roundtrip",
                    id="deployment-" + uuid4().hex,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=30),
                )
                == "persisted:roundtrip"
            )
        async with httpx.AsyncClient() as http:
            for _ in range(60):
                response = await http.get(stack.ui_address + "/api/v1/namespaces")
                if response.status_code == 200:
                    break
                await asyncio.sleep(0.5)
            assert response.status_code == 200, response.text
            assert "boxie" in response.text
        # Role separation is enforced by PostgreSQL CONNECT privileges, not naming.
        for user, password_key, allowed in (
            ("boxie", "BOXIE_DB_PASSWORD", "boxie"),
            ("temporal", "TEMPORAL_DB_PASSWORD", "temporal"),
            ("visibility", "VISIBILITY_DB_PASSWORD", "temporal_visibility"),
        ):
            for database in ("boxie", "temporal", "temporal_visibility"):
                parameters = dict(
                    host="127.0.0.1",
                    port=stack.pg_port,
                    dbname=database,
                    user=user,
                    password=stack.env[password_key],
                    connect_timeout=2,
                )
                if database == allowed:
                    with psycopg.connect(**parameters) as conn:
                        assert conn.execute("SELECT 1").fetchone() == (1,)
                else:
                    with pytest.raises(psycopg.OperationalError, match="permission denied"):
                        psycopg.connect(**parameters)

        operator = await Client.connect(
            stack.address, namespace="boxie", tls=tls(stack, "operator")
        )

        async def set_retention(seconds: int) -> None:
            await operator.workflow_service.update_namespace(
                UpdateNamespaceRequest(
                    namespace="boxie",
                    config=NamespaceConfig(
                        workflow_execution_retention_ttl=Duration(seconds=seconds)
                    ),
                ),
                timeout=timedelta(seconds=10),
            )

        await set_retention(86400)
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(stack.bootstrap)
        await set_retention(604800)
        # Repeating bootstrap against the existing namespace verifies idempotence
        # here, without repeating its propagation grace in every native fixture.
        await asyncio.to_thread(stack.bootstrap)
        stack.evidence["namespace"] = "repeated bootstrap verified existing seven-day retention"
        stack.evidence["security"] = (
            "valid API/worker/operator/UI; rejected no cert, foreign CA, wrong hostname; 6 cross-database denials"
        )
        stack.evidence["workflow"] = "real workflow and activity completed on production server"
        (stack.root / "evidence.json").write_text(json.dumps(stack.evidence, indent=2))

    asyncio.run(exercise())
