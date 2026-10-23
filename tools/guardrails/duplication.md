# New duplication advisory

Maintained source changes compare normalized clone fragments against the merge base using jscpd 4.0.5, with a minimum of five lines and 50 tokens. Existing clone counts are subtracted so line shifts are not reported as new duplication. Findings do not fail CI; scanner errors do. Generated files and designated fixture directories are excluded. Svelte scripts are extracted case-insensitively. Node.js 24 and npm are required for the command below.

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools python3 -m guardrails.duplication --base origin/master
```
