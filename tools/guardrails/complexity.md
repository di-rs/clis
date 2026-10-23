# Function complexity

Production functions in maintained Rust, Python and frontend code must have cyclomatic complexity ≤15, control-flow nesting ≤4 and length ≤100 code lines, excluding blanks and comment-only lines. Existing functions over any limit may not worsen any metric. Test functions are advisory; generated code and fixtures are excluded. Renamed functions are treated as new. Svelte script blocks preserve diagnostic line offsets and accept either tag case.

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools uv run python -m guardrails.complexity --base origin/master
```
