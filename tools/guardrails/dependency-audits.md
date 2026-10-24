# Dependency vulnerability audits

Python and frontend dependency/configuration changes select independent audit jobs. Every known vulnerability, including development dependencies and advisories without fixes, fails CI; no exceptions are supported. Unavailable or incomplete audits report that state without failing. Python exports all frozen workspace groups and uses pip-audit 2.10.1 without dependency installation. Frontend audits an isolated lockfile copy with repository audit ignores removed. Use uv 0.13 and install pip-audit 2.10.1; pnpm is supplied by the frontend toolchain.

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools python3 -m guardrails.dependency_audits python
PYTHONPATH=tools python3 -m guardrails.dependency_audits frontend
```
