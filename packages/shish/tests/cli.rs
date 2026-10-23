use assert_fs::prelude::*;
use cli_testing::cargo_bin_cmd;

#[test]
fn reports_version() {
    cargo_bin_cmd!()
        .arg("--version")
        .assert()
        .code(0)
        .stdout("shish 0.1.0\n")
        .stderr("");
}

#[test]
fn runs_an_external_program() {
    cargo_bin_cmd!()
        .args(["-c", "/bin/echo imported"])
        .assert()
        .code(0)
        .stdout("imported\n")
        .stderr("");
}

#[test]
fn runs_relative_executable_without_path_lookup() {
    use assert_fs::prelude::*;
    use std::os::unix::fs::PermissionsExt as _;
    let home = assert_fs::TempDir::new().unwrap();
    let script = home.child("local-script");
    script.write_str("#!/bin/sh\necho local\n").unwrap();
    std::fs::set_permissions(script.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    cargo_bin_cmd!()
        .current_dir(home.path())
        .env("PATH", "/missing")
        .args(["-c", "./local-script"])
        .assert()
        .code(0)
        .stdout("local\n")
        .stderr("");
}

#[test]
fn waits_for_every_pipeline_stage() {
    use assert_fs::prelude::*;
    let home = assert_fs::TempDir::new().unwrap();
    home.child("worker")
        .write_str("exec >/dev/null 2>&1\n/bin/sleep 0.2\nprintf done > completed\n")
        .unwrap();
    cargo_bin_cmd!()
        .current_dir(home.path())
        .args(["-c", "/bin/sh worker | /usr/bin/true"])
        .timeout(std::time::Duration::from_secs(3))
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    home.child("completed").assert("done");
}

#[test]
fn evaluates_mixed_conditional_chains_after_skipping() {
    for script in [
        "/usr/bin/false && /bin/echo skipped || /bin/echo recovery",
        "/usr/bin/true || /bin/echo skipped && /bin/echo recovery",
    ] {
        cargo_bin_cmd!()
            .args(["-c", script])
            .assert()
            .code(0)
            .stdout("recovery\n")
            .stderr("");
    }
}

#[test]
fn exits_on_eof_without_prompt() {
    cargo_bin_cmd!()
        .write_stdin("")
        .timeout(std::time::Duration::from_secs(1))
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
}

#[test]
fn piped_input_has_no_prompt() {
    cargo_bin_cmd!()
        .write_stdin("/bin/echo piped\n")
        .timeout(std::time::Duration::from_secs(1))
        .assert()
        .code(0)
        .stdout("piped\n")
        .stderr("");
}

#[test]
fn invalid_command_reports_a_diagnostic_without_panicking() {
    cargo_bin_cmd!()
        .args(["-c", "shish_missing_command"])
        .env("PATH", "")
        .assert()
        .code(127)
        .stdout("")
        .stderr("error: command not found: shish_missing_command\n");
}

#[test]
fn syntax_check_does_not_execute_or_redirect() {
    let directory = assert_fs::TempDir::new().unwrap();
    cargo_bin_cmd!()
        .current_dir(&directory)
        .args(["-n", "-c", "printf executed > created; exit 42"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    directory
        .child("created")
        .assert(predicates::path::missing());
}

#[test]
fn syntax_check_accepts_multiline_stdin() {
    cargo_bin_cmd!()
        .arg("-n")
        .write_stdin("for value in a b; do\n printf '%s\\n' \"$value\"\ndone\n")
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
}

#[test]
fn syntax_error_reports_a_location_and_status_two() {
    cargo_bin_cmd!()
        .args(["-n", "-c", "echo before; )"])
        .assert()
        .code(2)
        .stdout("")
        .stderr("shish: syntax error at line 1 col 14\n");
}

#[test]
fn checkout_debug_programs_do_not_override_trusted_commands() {
    use std::os::unix::fs::PermissionsExt as _;
    let directory = assert_fs::TempDir::new().unwrap();
    let program = directory.child("target/debug/echo");
    program.write_str("#!/bin/sh\nprintf hijacked\n").unwrap();
    std::fs::set_permissions(program.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    cargo_bin_cmd!()
        .current_dir(&directory)
        .env("PATH", "/usr/bin:/bin")
        .args(["-c", "echo hello"])
        .assert()
        .code(0)
        .stdout("hello\n")
        .stderr("");
}

// Acceptance tests for documented published-Brush limitations. See
// docs/compatibility-gaps.md.
#[test]
#[ignore = "Brush 0.5.0 cached PATH lookup selects directories; awaiting a published fix"]
fn path_lookup_skips_directories_and_nonexecutable_files() {
    use std::os::unix::fs::PermissionsExt as _;
    let directory = assert_fs::TempDir::new().unwrap();
    directory.child("first/probe").create_dir_all().unwrap();
    let blocked = directory.child("second/probe");
    blocked.write_str("not executable").unwrap();
    std::fs::set_permissions(blocked.path(), std::fs::Permissions::from_mode(0o644)).unwrap();
    let executable = directory.child("third/probe");
    executable.write_str("#!/bin/sh\nprintf found\n").unwrap();
    std::fs::set_permissions(executable.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    let paths = std::env::join_paths([
        directory.child("first").path(),
        directory.child("second").path(),
        directory.child("third").path(),
    ])
    .unwrap();
    cargo_bin_cmd!()
        .current_dir(&directory)
        .env("PATH", paths)
        .args(["-c", "probe"])
        .assert()
        .code(0)
        .stdout("found")
        .stderr("");
}

#[test]
#[ignore = "stdin completeness reparses growing prefixes; awaiting a published incremental parser"]
fn large_multiline_stdin_completes_within_budget() {
    let directory = assert_fs::TempDir::new().unwrap();
    let script = directory.child("large.sh");
    script
        .write_str(&format!(
            "value='\n{}'\nprintf done\n",
            "payload\n".repeat(20_000)
        ))
        .unwrap();
    // A regular file avoids assert_cmd's unbounded pipe writer on timeout.
    // This operational budget is not a POSIX timing requirement.
    let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_shish"));
    command
        .arg("-s")
        .current_dir(&directory)
        .env("LC_ALL", "C")
        .env("SANCTUARY_LOG_LEVEL", "off")
        .stdin(std::fs::File::open(script.path()).unwrap());
    command
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    let mut child = command.spawn().unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        if child.try_wait().unwrap().is_some() {
            break;
        }
        if std::time::Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("multiline stdin exceeded the five-second acceptance budget");
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let output = child.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(output.stdout, b"done");
    assert_eq!(output.stderr, b"");
}
