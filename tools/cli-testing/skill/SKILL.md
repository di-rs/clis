---
name: cli-testing
description: Operate the installed Sanctuary cli-testing command to discover TOML CLI cases, inspect expectations, compare built candidates with independent references, and stage or promote fixture recordings. Use for catalog and reference-fixture workflows, not general test authoring unrelated to this tool.
---

# CLI testing command

Check `command -v cli-testing`, then `cli-testing --json doctor`. If missing, run
`make install-cli-testing` in the Sanctuary checkout and ensure `~/.local/bin` is
on PATH. The tool is offline and requires no authentication or user config.

Use `discover --root DIR` to find catalogs. Its `suite` paths are absolute and work
from any cwd. Pass one with `--suite PATH` for all case operations. `list` discovers
case names; `resolve NAME` confirms an exact name and execution owner. Names are
stable IDs within their suite. `list` and `discover` default to 50 entries; follow
`data.next_offset` with `--offset` until null when more entries are needed.

Start with reads:

```sh
cli-testing --json doctor
cli-testing --json discover --root . --limit 10
cli-testing --json --suite buildins/cutr/tests/cases.toml explain tsv_b8
```

Use `validate` to check recipes and fixture ownership. Use `compare --candidate
/absolute/path/to/binary` for automatic cases; it does not build the candidate.
Cases marked `native_required` need native Cargo/nextest tests. `catalog` is the
read-only escape hatch for the complete recipe model; it never executes recipes.

For authorized fixture generation, inspect the reference, locale, arguments, and
inputs first. Pilot references use macOS BSD tools; never substitute a different
implementation or record the candidate as its own oracle. References are trusted
repository commands and run with the caller's permissions.

`record --output NEW_DIR` stages raw output and provenance outside `tests/`.
Review the completed manifest and raw streams, then use `apply DIR --dry-run` to
preflight changed destinations. Promote with `apply DIR` only within the user's
authorized fixture-update scope. A preview does not grant authorization. Stop on
stale/incomplete/conflicting recordings, correct the cause, and record into a new
directory. Never silently overwrite expectations to make failing tests pass.
Rerun the owning package's native and catalog tests after promotion.

Under `--json`, stdout is one envelope with `schema`, `ok`, `data`, and `error`.
Use both exit status and `ok`; comparison failures retain per-case statuses in
`data.cases`. Codes are 0 success, 1 operation failure, and 2 argument error.
JSON errors omit raw parser/child diagnostics; rerun without `--json` for detail
when appropriate. Explicit catalog reads expose fixture values, so do not put
credentials in catalogs or copy sensitive diagnostics into reports.
