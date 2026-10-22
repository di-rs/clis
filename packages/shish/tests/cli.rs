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
        .code(1)
        .stdout("")
        .stderr("Error: shish_missing_command: command not found\n");
}
