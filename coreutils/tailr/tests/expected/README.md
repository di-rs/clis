# Tail fixture and reference provenance

Existing `.out` fixtures predate recorded capture provenance. Their originating
executable/version/date cannot be reconstructed reliably; they remain historical
regression fixtures, not independently certified GNU output.

The live [case manifest](../reference_cases.json) assigns stable IDs to 97 cases:
empty/binary/CRLF/unterminated inputs, line/byte counts 0/1/99/+0/+1/+2, seekable
files and explicit stdin, plus quiet multi-file output. Each case records exact
input bytes, argv, expected status and timeout. No output normalization is used.
The checker captures both processes' raw stdout/stderr/status, GNU executable and
version, OS, locale/timezone and input hashes in a new output directory.

Native macOS validation on 2026-10-06 used GNU Coreutils 9.12 at
`/opt/homebrew/bin/gtail`; all 97 cases matched. The local evidence directory is
`target/reference/initial`. CI artifacts preserve the exact reference installed
on each runner; Linux and macOS evidence must be assessed separately.

From the repository root, after `cargo build --locked -p tailr`:

```sh
python3 scripts/reference_check.py --reference /absolute/GNU/tail \
  --candidate "$PWD/target/debug/tailr" \
  --cases coreutils/tailr/tests/reference_cases.json \
  --output-dir target/reference/new-capture
```

The package's `mk-outs.nu` offers the same interface for Nushell 0.116.1:
`nu coreutils/tailr/mk-outs.nu --reference /absolute/GNU/tail --candidate /absolute/tailr --output /empty/capture/dir`.
Review capture diffs before adopting any expected output. Existing fixtures and
checkout inputs are not deleted or rewritten. Ordinary Rust tests require neither
GNU tools nor Nushell. Missing or non-GNU references fail explicitly.

Excluded differences remain in the [app contract](../../README.md): default stdin
when operands are omitted, follow mode, suffix syntax and file-open exit status.
This matrix does not certify all GNU tail behavior or other utilities.
