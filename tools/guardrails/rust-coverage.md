# Rust changed-line coverage

Changes to Rust source, dependencies or coverage configuration run the workspace through cargo-llvm-cov and nextest. CI reports covered added/modified executable lines with no minimum threshold. Repeated LCOV records are deduplicated by path and line. No instrumented changed lines yields N/A. Test/build failures still fail the job. To inspect an existing LCOV report locally:

Run from the repository root with the workspace environment available:

```bash
PYTHONPATH=tools python3 -m guardrails.rust_coverage --base origin/master --report target/coverage/lcov.info
```
