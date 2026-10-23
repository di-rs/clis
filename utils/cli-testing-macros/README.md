# cli-testing-macros

This procedural-macro crate implements `#[test_case("name")]`. Consumers use its
re-export from [cli-testing](../cli-testing/README.md#customize-a-named-case-in-rust);
they do not need a separate dependency on this crate.

The macro validates the function signature and generates a normal `#[test]`
wrapper. It preserves the original name and attributes, then delegates case
loading, customization, execution, and assertions to `cli_testing::customize`.
Catalogs are read at runtime, so the macro has no fixture or TOML parsing logic.

Compiler-diagnostic tests cover unsupported signatures and conflicting test
attributes. The cli-fixtures integration tests exercise generated wrappers,
including unit/Result returns, early execution, `cfg`, `ignore`, and failures.

```sh
cargo test -p cli-testing-macros
cargo nextest run -p cli-fixtures -p cli-testing --profile ci
```
