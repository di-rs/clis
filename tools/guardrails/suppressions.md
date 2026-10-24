# Suppression reasons

New lint/type suppressions and skipped tests in maintained Rust, Python and frontend source must have an adjacent `reason: ...` explanation of at least three words, or a native reason argument. Issue links are optional. Blanket/file-wide suppressions fail even with a reason. Existing directives are grandfathered; additions and removals are reported separately. Generated code and designated fixture directories are excluded. The parser analyzes Svelte script blocks case-insensitively; it never executes source.

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools uv run python -m guardrails.suppressions --base origin/master
```
