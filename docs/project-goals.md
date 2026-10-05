# Project goals and library use

[Project overview](../README.md) · [Architecture](architecture.md)

## Project goals

| Goal | Evidence required |
| --- | --- |
| GNU and BSD compatibility | Versioned reference behavior, a per-utility option/behavior matrix, and regression tests for parsing, output, status, and side effects. |
| Better performance | Correctness-checked, reproducible comparisons against reference tools and the previous Rust implementation, with workload and platform stated. |
| Library-first design | The CLI and Rust callers use the same implementation through typed, documented APIs without process-global setup. |
| Consistent, maintainable code | Thin CLI adapters, focused domain modules, tested shared mechanisms, workspace conventions, and explained trade-offs. |

Correctness and API boundaries apply throughout development, not after optimization.
"GNU and BSD" does not mean one ambiguous behavior: when implementations disagree,
the utility must document how the conflict is resolved. See the
[compatibility policy](compatibility.md).

## Using a utility as a library

The target for every app is a reusable operation behind the CLI, not a wrapper
that launches a subprocess. Existing APIs vary; inspect the package's `src/lib.rs`
and README before depending on it. For example, `catr` already accepts explicit
readers, writers, and options:

```rust
fn main() -> std::io::Result<()> {
    let options = catr::Flags {
        number_lines: true,
        number_nonblank_lines: false,
        squeeze_blank: false,
    };
    let mut output = Vec::new();
    catr::write_lines(&b"alpha\nbeta\n"[..], &mut output, &options)?;
    assert_eq!(output, b"     1\talpha\n     2\tbeta\n");
    Ok(())
}
```

This example belongs in a consumer crate with a dependency on the local `catr`
package. For a consumer beside this checkout, its `Cargo.toml` can use:

```toml
[dependencies]
catr = { path = "../clis/coreutils/catr" }
```

The example demonstrates the existing UTF-8-oriented API, not full `cat` parity.
The [architecture guide](architecture.md) defines how to evolve these APIs
without leaking Clap, global streams, or process exit behavior into libraries.
