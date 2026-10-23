# Source Spelling

The check runs only when matching maintained files change or its configuration changes and a matching file type remains in the repository. Configuration changes revalidate all matching files. Ordinary changes inspect changed files only. Findings fail CI. Generated/vendored paths and symlinks are excluded. Install the tool/version specified in the workflow before running locally.

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools python3 -m guardrails.source_spelling --base origin/master
```
