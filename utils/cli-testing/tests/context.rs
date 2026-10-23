#![cfg(unix)]
#![allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify public test contracts; Result propagates setup failures."
)]

use assert_fs::TempDir;
use cli_testing::{Error, TestContext, catalog::Suite, customize};
use std::{
    fs,
    panic::{AssertUnwindSafe, catch_unwind},
    path::Path,
};

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

fn package(cases: &str) -> Result<TempDir> {
    let dir = TempDir::new()?;
    fs::create_dir_all(dir.path().join("tests/inputs"))?;
    fs::create_dir_all(dir.path().join("tests/expected"))?;
    fs::write(dir.path().join("Cargo.toml"), "[package]\nname = 'probe'\n")?;
    fs::write(
        dir.path().join("tests/cases.toml"),
        format!("schema = 1\nbinary = 'probe'\n{cases}"),
    )?;
    Ok(dir)
}

fn shell(script: &str, stdout: &str) -> Result<String> {
    Ok(format!(
        r#"
[[cases]]
name = "sample"
execution = "rust"
args = {}
origin = {{ manual = "Controlled shell probe of the test runtime." }}
expect = {{ exit = 0, stdout = {}, stderr = "" }}
"#,
        serde_json::to_string(&["-c", script])?,
        serde_json::to_string(stdout)?
    ))
}

#[test]
fn early_execution_is_cached_and_configuration_freezes() -> Result {
    let dir = package(&shell("printf x >> \"$MARKER\"; printf ok", "ok")?)?;
    let marker = dir.path().join("runs");
    customize(&TestContext::new("/bin/sh", dir.path()), "sample", |cx| {
        cx.env("MARKER", &marker)?;
        assert_eq!(cx.run()?.stdout, b"ok");
        assert_eq!(cx.run()?.stdout, b"ok");
        assert!(cx.args(["extra"]).is_err());
        assert!(cx.env("LC_ALL", "other").is_err());
        assert!(cx.current_dir("/").is_err());
        assert!(cx.write_stdin("extra").is_err());
        assert!(cx.map_expected_stdout(<[u8]>::to_vec).is_err());
        assert!(cx.expect_stderr("").is_err());
        assert!(cx.expect_exit(1).is_err());
        Ok(())
    })?;
    assert_eq!(fs::read(marker)?, b"x");
    Ok(())
}

#[test]
fn automatic_execution_retains_workspace_until_checks_finish() -> Result {
    let dir = package(&shell("cat data", "original")?)?;
    fs::write(dir.path().join("tests/inputs/data"), b"original")?;
    let mut location = None;
    customize(&TestContext::new("/bin/sh", dir.path()), "sample", |cx| {
        let workspace = cx.workspace()?;
        workspace.copy_input("data", "data")?;
        location = Some(workspace.path().to_path_buf());
        Ok(())
    })?;
    assert!(!location.ok_or("no workspace created")?.exists());
    assert_eq!(fs::read(dir.path().join("tests/inputs/data"))?, b"original");
    Ok(())
}

#[test]
fn customization_errors_and_panics_never_launch_the_candidate() -> Result {
    let dir = package(&shell("printf launched > marker", "")?)?;
    let context = TestContext::new("/bin/sh", dir.path());
    let error = customize(&context, "sample", |_| Err(Error::new("custom failure")));
    assert!(error.is_err_and(|error| error.to_string().contains("custom failure")));
    let panic = catch_unwind(AssertUnwindSafe(|| {
        customize(&context, "sample", |_| {
            std::panic::resume_unwind(Box::new("custom panic"));
        })
    }));
    assert!(panic.is_err());
    assert!(!dir.path().join("marker").exists());
    Ok(())
}

#[test]
fn failed_spawn_is_not_retried_and_configuration_stays_frozen() -> Result {
    let dir = package(&shell("", "")?)?;
    let program = dir.path().join("not-created");
    let mut cx = TestContext::new(&program, dir.path()).case("sample")?;
    assert!(cx.run().is_err());
    std::os::unix::fs::symlink("/bin/sh", &program)?;
    assert!(
        cx.run()
            .is_err_and(|error| error.to_string().contains("previously failed"))
    );
    assert!(cx.env("X", "Y").is_err());
    Ok(())
}

#[test]
fn raw_concatenated_stdin_and_regular_file_transport_are_distinct() -> Result {
    let dir = package(
        r#"
[[cases]]
name = "pipe"
args = ["-c", "test -p /dev/stdin || exit 7; cat"]
stdin = { mode = "pipe", files = ["inputs/first", "inputs/last"] }
origin = { manual = "Raw byte concatenation through a real pipe." }
expect = { exit = 0, stdout_file = "expected/raw", stderr = "" }
[[cases]]
name = "file"
args = ["-c", "test -f /dev/stdin || exit 8; cat"]
stdin = { mode = "file", path = "inputs/all" }
origin = { manual = "A regular file remains seekable stdin." }
expect = { exit = 0, stdout_file = "expected/raw", stderr = "" }
"#,
    )?;
    let raw = b"\0\xff\r\nlast";
    for (name, bytes) in [
        ("inputs/first", b"\0\xff\r\n".as_slice()),
        ("inputs/last", b"last"),
        ("inputs/all", raw),
        ("expected/raw", raw),
    ] {
        fs::write(dir.path().join("tests").join(name), bytes)?;
    }
    let context = TestContext::new("/bin/sh", dir.path());
    context.case("pipe")?.check()?;
    context.case("file")?.check()?;
    Ok(())
}

#[test]
fn exact_checks_reject_status_stderr_and_byte_differences() -> Result {
    for (script, expected, diagnostic) in [
        ("printf 'a\\r\\n'", "a\n", "stdout differs"),
        ("printf a", "a\n", "stdout differs"),
        ("printf err >&2", "", "stderr differs"),
        ("exit 3", "", "expected exit 0"),
    ] {
        let dir = package(&shell(script, expected)?)?;
        let result = customize(&TestContext::new("/bin/sh", dir.path()), "sample", |_| {
            Ok(())
        });
        assert!(
            result.is_err_and(|error| error.to_string().contains(diagnostic)),
            "{diagnostic}"
        );
    }
    Ok(())
}

#[test]
fn contexts_isolate_workspaces_and_share_command_defaults() -> Result {
    let dir = package(&shell("", "")?)?;
    fs::write(dir.path().join("tests/inputs/source"), b"fixture")?;
    let test = TestContext::new("/bin/sh", dir.path());
    let left = test.workspace()?;
    let right = test.workspace()?;
    left.copy_input("source", "nested/copied")?;
    assert!(!right.child("nested/copied").path().exists());
    left.command()
        .args([
            "-c",
            "printf '%s' \"$LC_ALL/$TZ/$TERM/$SANCTUARY_LOG_LEVEL\"",
        ])
        .assert()
        .code(0)
        .stdout("C/UTC/dumb/off")
        .stderr("");
    let command = left.std_command();
    assert_eq!(command.get_current_dir(), Some(left.path()));
    assert!(left.copy_input("source", "../escape").is_err());
    Ok(())
}

#[test]
fn catalogs_reject_unknown_fields_duplicates_invalid_paths_and_missing_files() -> Result {
    let valid = shell("", "")?;
    let invalid = [
        format!("unknown = 1\n{valid}"),
        format!("{valid}\n{valid}"),
        valid.replace("args =", "typo ="),
        valid.replace("stdout = \"\"", "stdout_file = \"expected/missing\""),
        valid.replace("stdout = \"\"", "stdout_file = \"expected/../outside\""),
        valid.replace("stdout = \"\"", "stdout_file = \"/tmp/outside\""),
        valid.replace("exit = 0", "exit = 256"),
        valid.replace("manual =", "unknown ="),
    ];
    for text in invalid {
        let dir = package(&text)?;
        assert!(
            Suite::load(dir.path().join("tests/cases.toml")).is_err(),
            "accepted {text}"
        );
    }
    let dir = package(&valid)?;
    let test = TestContext::new("/bin/sh", dir.path());
    assert!(test.case("absent").is_err());
    fs::write(
        dir.path().join("tests/cases.toml"),
        format!(
            "schema = 1\nbinary = 'probe'\n{}",
            valid.replace("execution = \"rust\"", "")
        ),
    )?;
    assert!(
        customize(&test, "sample", |_| Ok(()))
            .is_err_and(|error| error.to_string().contains("execution = \"rust\""))
    );
    let suite = test.suite()?;
    std::os::unix::fs::symlink("/etc/hosts", dir.path().join("tests/inputs/link"))?;
    assert!(suite.fixture_path(Path::new("inputs/link")).is_err());
    Ok(())
}

#[test]
fn adaptation_changes_only_expected_bytes_and_requires_a_reason() -> Result {
    let cases = shell("printf '\\357\\277\\275'", "")?
        .replace(
            "manual = \"Controlled shell probe of the test runtime.\"",
            "reference = \"source\"",
        )
        .replace("stdout = \"\"", "stdout_file = \"expected/raw\"");
    let recipe = format!(
        "[references.source]\nprogram = '/bin/sh'\nplatform = '{}'\n{cases}\nadapt = {{ expected_stdout = 'utf8-lossy', reason = 'Replace a split UTF-8 sequence in reference bytes.' }}\n",
        std::env::consts::OS
    );
    let dir = package(&recipe)?;
    fs::write(dir.path().join("tests/expected/raw"), [0xff])?;
    let test = TestContext::new("/bin/sh", dir.path());
    test.case("sample")?.check()?;
    let catalog = dir.path().join("tests/cases.toml");
    // If actual output were also decoded lossily, this invalid byte would pass.
    let original = fs::read_to_string(&catalog)?;
    fs::write(
        &catalog,
        original.replace("\\\\357\\\\277\\\\275", "\\\\377"),
    )?;
    assert!(test.case("sample")?.check().is_err());
    fs::write(
        &catalog,
        original.replace(
            "reason = 'Replace a split UTF-8 sequence in reference bytes.'",
            "reason = ''",
        ),
    )?;
    assert!(test.suite().is_err());
    Ok(())
}

#[test]
fn missing_expectation_fails_before_customization() -> Result {
    let text = shell("printf launched > marker", "")?
        .replace("stdout = \"\"", "stdout_file = \"expected/missing\"");
    let dir = package(&text)?;
    let mut called = false;
    let result = customize(&TestContext::new("/bin/sh", dir.path()), "sample", |_| {
        called = true;
        Ok(())
    });
    assert!(result.is_err());
    assert!(!called);
    assert!(!dir.path().join("marker").exists());
    Ok(())
}
