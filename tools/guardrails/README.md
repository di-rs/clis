# PR guardrails

These checks keep review policy separate from language builds. PR jobs run only
when relevant source, dependencies, or check configuration changes. A deleted
source file still selects its language's tests. A documentation-only PR skips
Rust, Python, frontend builds, coverage, and dependency audits.

## Policy

| Check | Behavior |
| --- | --- |
| PR size | Report additions + deletions; 200 review lines is advisory, never a failure |
| Suppressions and skips | New directives need an adjacent `reason: explanation` or a tool-native reason argument; no issue link required |
| Blanket suppressions | New file/module-wide or unqualified blanket suppressions fail, even with a reason |
| Complexity | Production functions: cyclomatic complexity ≤15, control nesting ≤4, code length ≤100 lines |
| Existing complexity debt | If a function already exceeds any limit, none of its three metrics may increase |
| Test complexity | Report regressions without failing |
| Rust coverage | Report coverage of changed executable lines; no coverage minimum |
| Duplication | Report additional clone pairs, minimum 5 lines and 50 tokens; never fail on duplication |
| Shell | ShellCheck validates all maintained shell scripts when a shell file or its policy changes |
| YAML | Syntax, duplicate keys, trailing spaces, and final newline; errors fail |
| Markdown | Minimal structural rules in the root configuration; violations fail |
| Spelling | Typos checks maintained source, identifiers, configuration, and documentation |
| Dependency audits | Python and frontend: every known vulnerability fails, including development dependencies and advisories without fixes; no exceptions |
| Audit unavailable | Report **incomplete** without failing; never report an incomplete audit as clean |
| TruffleHog | Advisory evaluation without credential verification or network access; Gitleaks remains the secret gate |

Reasons must contain at least three words. Examples:

```python
# reason: upstream package does not publish type stubs
import external_package  # type: ignore[import-untyped]
```

```rust
#[allow(clippy::unwrap_used, reason = "constant was validated above")]
fn example() {}
```

Explain why the exception is necessary. The check verifies that text is present;
it cannot decide whether the justification is convincing. Existing unchanged
exceptions are not retroactively rejected. Additions and removals are counted
separately, so removing one exception cannot offset a new undocumented one.

## Selection and exclusions

[Path selection](changes.py) is shared by PR validation, CodeQL, and the new
[guardrail workflow](../../.github/workflows/guardrails.yml). It inspects the full
Git diff against the merge base, rather than GitHub's truncated changed-file
payload. Dependency and tool configuration changes select the relevant checks.
Changing the guardrail implementation revalidates its supported checks.

Content linters check changed maintained files; changing their own configuration
revalidates the full matching file set. ShellCheck always checks all maintained
shell files once selected. Existing language builds/tests remain broad within
selected languages. CodeQL selects Rust, Python, and Actions independently on PRs;
scheduled and master CodeQL scans retain all three.

Generated/vendor directories, build outputs, installed dependencies, and shared
agent configuration are excluded from review-size and source reports. Lockfiles
are excluded from review size but still select dependency audits. `fixtures`,
`inputs`, and `expected` directories are designated fixtures excluded from
complexity and duplication. Typos has its own explicit vocabulary and exclusions
in [typos.toml](../../typos.toml).

Rust inline test attributes and conventional test directories/filenames select
advisory complexity reporting. Svelte analysis covers its script blocks, not its
template markup. Metrics use Tree-sitter syntax trees: function length counts
lines containing code tokens, and nesting counts nested control constructs.
Cyclomatic complexity counts decisions and short-circuit Boolean operators.
Functions are matched by qualified name and occurrence order within the same
file; moves and renames are conservatively treated as new functions. Syntax
unsupported by the pinned parsers fails analysis rather than silently passing.

## Run locally

From the repository root:

```sh
uv sync --all-packages
PYTHONPATH=tools uv run python -m guardrails select --base origin/master
PYTHONPATH=tools uv run python -m guardrails policy --base origin/master
PYTHONPATH=tools uv run python -m guardrails size --base origin/master
uv run pytest tools/guardrails/tests
```

Comparisons use committed Git objects (`HEAD` by default), not unstaged edits.
Use `--head COMMIT` to select another commit. Successful checks and advisory
reports exit 0; policy violations exit 1. Operational errors also fail, except
explicitly incomplete dependency audits and the exploratory secret scan.
Reports appear on stdout and, in Actions, in the job summary.

Coverage consumes an LCOV file through `coverage --report PATH`. Duplication
requires Node/npm and downloads pinned jscpd 4.0.5 through `npx`; it materializes
source snapshots as plain files and never runs their code. Markdown checking
similarly uses pinned markdownlint-cli2. ShellCheck and Typos must be available
on PATH for their respective modes. Python audit uses the frozen uv workspace
export; frontend audit requires pnpm and reads the frontend lockfile in an
isolated directory without repository audit-ignore settings.

## Labels and badge

The metadata-only label workflow maintains `area:rust`, `area:python`,
`area:frontend`, `area:ci`, `area:docs`, and `area:infra`. It never checks out PR
content. Other labels remain untouched.

After a source change lands on master, the badge workflow counts nonblank lines
in maintained source and publishes `loc.svg` on the `badges` branch. It includes
comments and is a size indicator, not a quality score. Badge generation uses a
read-only job; a separate job writes the artifact without executing repository
code. The badge is unavailable until the first successful master run.
