# Biggie library and features

CSV is enabled by default. Disable it with
`cargo build --release -p biggie --no-default-features`, or enable it
explicitly with `--no-default-features --features csv`. A build without CSV
supports plain fields and rejects `--format csv` before touching output.

Rust callers use `generate_text`, `generate_records`, `generate_fields`,
`generate_bytes` and `generate_pair` with their corresponding typed options and
caller-owned writers. These operations return `anyhow::Result<()>`, validate
before writing, retain I/O causes for downcasting and never flush caller writers.
The older ASCII entry points `generate` and `gen_random_lines` retain `io::Result`.
See the [API examples](../src/lib.rs) and operation-module doctests.

The same seed/options/build/platform reproduce the same random data; bytes may
change across rand versions or platforms. Biggie has no direct GNU/BSD counterpart.

[Workspace README](../../../README.md)
