"""Bounded, allowlisted evidence when the native lifecycle readiness wait fails."""

import json
import re
from typing import Any

AGENT_PROBE = """
import json
from pathlib import Path
import httpx
from prometheus_client.parser import text_string_to_metric_families
result = {}
credential = Path('/run/secrets/agent').read_text().strip()
with httpx.Client(timeout=2, trust_env=False) as client:
    try:
        response = client.get('http://agent:8091/readyz', headers={'Authorization': 'Bearer ' + credential})
        values = response.json().get('dependencies', {})
        result['agent'] = {'status': response.status_code, 'dependencies': {
            name: values.get(name) if type(values.get(name)) is bool else None
            for name in ('docker', 'postgres', 'execution')
        }}
    except Exception as error:
        result['agent'] = {'error_type': type(error).__name__}
    try:
        response = client.get('http://127.0.0.1:9101/metrics')
        response.raise_for_status()
        if len(response.content) > 65536:
            raise ValueError('metrics size limit')
        result['dependencies'] = {}
        for family in text_string_to_metric_families(response.text):
            for sample in family.samples:
                name = sample.labels.get('dependency')
                if sample.name in ('boxie_dependency_known', 'boxie_dependency_ready') and name in ('agent', 'temporal', 'namespace', 'workflow_poller', 'activity_poller') and sample.value in (0, 1):
                    result['dependencies'].setdefault(sample.name, {})[name] = sample.value
    except Exception as error:
        result['metrics_error_type'] = type(error).__name__
print(json.dumps(result))
"""


def readiness_details(stack: Any) -> str:
    result: dict[str, Any] = {"containers": {}}
    for service in ("agent", "api", "worker", "faults"):
        try:
            identifier = stack.runtime_command("ps", "--all", "--quiet", service)
            if not re.fullmatch("[a-f0-9]{64}", identifier):
                raise ValueError("exact service identity unavailable")
            item = json.loads(stack.command(["docker", "inspect", identifier]))[0]
            labels = item["Config"]["Labels"]
            if (
                labels.get("com.docker.compose.project") != stack.runtime
                or labels.get("com.docker.compose.service") != service
            ):
                raise ValueError("foreign readiness diagnostic container")
            state = item["State"]
            result["containers"][service] = {
                "id": identifier,
                "status": state["Status"]
                if state["Status"]
                in ("created", "running", "paused", "restarting", "removing", "exited", "dead")
                else "unknown",
                "running": state["Running"] is True,
                "oom_killed": state["OOMKilled"] is True,
                "exit_code": int(state["ExitCode"]),
            }
        except Exception as error:
            result["containers"][service] = {"error_type": type(error).__name__}
    try:
        output = stack.runtime_command(
            "exec", "-T", "api", "timeout", "8", "python", "-c", AGENT_PROBE
        )
        if len(output) > 4096:
            raise ValueError("readiness diagnostic size limit")
        result["probes"] = json.loads(output)
    except Exception as error:
        result["probes"] = {"error_type": type(error).__name__}
    return json.dumps(result)
