# PR size advisory

Every non-draft PR reports additions plus deletions against a 200-line review limit. Lockfiles, generated files and vendored files are excluded from the review count; the total is also shown. Binary files have no line count. Exceeding the limit is advisory.

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools python3 -m guardrails.pr_size --base origin/master
```
