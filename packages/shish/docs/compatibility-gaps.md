# Known compatibility gaps


Keep published Brush dependencies while these gaps remain open. Neither the
acceptance tests below nor the existing smoke tests establish full POSIX
conformance. Evidence was collected on macOS 27.0.1 (arm64) with shish 0.1.0, brush-core 0.5.0,
and brush-parser 0.4.0 in the default sh/POSIX mode. Other platforms are unverified.
The reference is `/bin/sh` (Bash 3.2.57 in sh mode); it passes both fixtures.
The ignored tests in [the CLI suite](../tests/cli.rs) assert the desired behavior;
they are not passing support claims.

### Large multiline stdin: partial support

The stdin adapter clones and reparses the entire pending command after every
line. A multiline quoted assignment containing 20,000 `payload` lines, followed
by `printf done`, exceeds the five-second acceptance budget before producing
output. The same input should finish with status 0, stdout `done`, and empty
stderr. Small multiline commands work; generated loops, here-documents, and
quoted strings can incur quadratic input-processing cost. This is an integration
performance gap, not a POSIX timing violation.

Run the bounded reproducer from the repository root:

```sh
cargo test -p shish --test cli large_multiline_stdin_completes_within_budget -- --ignored --exact
```

As a workaround, save generated code in a script and invoke `shish script.sh`.
Script operands use Brush's script API and avoid the adapter's prefix loop.
Reading all stdin before execution is not an equivalent fix: `read` and external
programs must retain access to their portion of stdin.

The follow-up is a separate PR adopting a published incremental command parser
and replacing the prefix loop. It must pass the acceptance test above and retain
`read`, external-program stdin, incomplete-EOF, and positional-argument behavior.
Brush's published parser currently exposes whole-program parsing; its tokenizer
is private. No upstream issue has been filed. Track the open
[PR #100 review finding](https://github.com/di-rs/sanctuary/pull/100#discussion_r4358515911).

### Directory entries in PATH: partial support

With `PATH=first:second:third`, put a directory named `probe` in `first`, a
non-executable file named `probe` in `second`, and an executable `probe` that
prints `found` in `third`. Expected behavior is status 0, stdout `found`, and empty
stderr. Current execution returns 126 with a permission-denied diagnostic and
no stdout because lookup selects the first directory.

This conflicts with the executable search described in POSIX.1-2024,
[Shell Command Language, Command Search and Execution](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html#tag_19_09_01_04).
Brush's normal cached lookup checks executable permissions without excluding
directories; its separate path-search helper excludes them. No shish lookup
replacement is introduced. Use an explicit executable path or remove the
conflicting directory entry from PATH as a workaround.

```sh
cargo test -p shish --test cli path_lookup_skips_directories_and_nonexecutable_files -- --ignored --exact
```

The follow-up is a separate PR adopting a published Brush fix, enabling this test,
and checking normal dispatch, cached lookup, and explicit PATH assignments. No
upstream issue has been filed. Track the open
[PR #96 review finding](https://github.com/di-rs/sanctuary/pull/96#discussion_r4358495650).
The separate checkout-relative `target/debug` lookup vulnerability is removed by
the runtime replacement and covered by an active CLI regression.

