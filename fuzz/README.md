# Bounded fuzz checks

The separate workspace locks libfuzzer-sys and its dependencies without adding
fuzz dependencies to production members. Use the repository's dated nightly,
its rust-src component and cargo-fuzz 0.13.2:

```sh
cargo fetch --locked --manifest-path fuzz/Cargo.toml
host_target=$(rustc -vV | sed -n 's/^host: //p')
export CARGO_NET_OFFLINE=true
cargo fuzz build parsu_xml --target "$host_target"
git diff --exit-code -- Cargo.lock fuzz/Cargo.lock
cargo fuzz run parsu_xml fuzz/corpus/parsu_xml --target "$host_target" -- -runs=0 -max_len=4096 -timeout=5 -rss_limit_mb=1024
mkdir -p fuzz/runs/parsu_xml/corpus
cp -R fuzz/corpus/parsu_xml/. fuzz/runs/parsu_xml/corpus/
cargo fuzz run parsu_xml fuzz/runs/parsu_xml/corpus --target "$host_target" -- -max_total_time=15 -max_len=4096 -timeout=5 -rss_limit_mb=1024
git diff --exit-code -- Cargo.lock fuzz/Cargo.lock
```

Run from the repository root with cargo-fuzz on PATH. Replace `parsu_xml` with
`tail_bytes` to exercise the other target. Cargo-fuzz 0.13.2 has no `--locked`
flag: fetch the committed graph with Cargo, build/run offline, and reject any
lockfile change before accepting the result. CI shows each command directly and
checks the lockfiles after building, replaying seeds and running mutations.
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
