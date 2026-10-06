# Bounded fuzz checks

The separate workspace locks libfuzzer-sys and its dependencies without adding
fuzz dependencies to production members. Use the repository's dated nightly,
its rust-src component and cargo-fuzz 0.13.2:

```sh
bash scripts/fuzz.sh parsu_xml 15
bash scripts/fuzz.sh tail_bytes 15
```

Run from the repository root with cargo-fuzz on PATH. The wrapper locks Cargo's
build/metadata calls (cargo-fuzz itself has no `--locked` flag), replays reviewed
seeds, then runs mutations for the selected time. It also rejects lockfile changes.
Inputs are limited to 4096 bytes, five seconds per input and 1024 MiB RSS. These
are resource bounds, not completeness or panic-freedom guarantees for larger inputs.

`parsu_xml` feeds valid UTF-8, including malformed XML, to the public parser.
`tail_bytes` compares stream/seek results over the same bytes and boundary counts.
The root workspace forbids first-party unsafe code; libFuzzer's external runtime
and harness expansion are a separate tool workspace, not an unsafe-free dependency
claim. Both graphs are covered by dependency policy.

Reviewed regression seeds live in `corpus/`; runtime corpus and logs live in
`runs/`, and failures in `artifacts/`. CI retains these for 14 days. Minimize and
reproduce a failure, add a deterministic Rust regression and a reviewed seed,
then verify the fix; never catch a panic or discard the input to obtain a pass.
PRs run 15 seconds per target; main, weekly and manual runs use 300 seconds.
