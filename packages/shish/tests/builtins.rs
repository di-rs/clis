use assert_fs::prelude::*;
use cli_testing::cargo_bin_cmd;

#[test]
fn status_builtins_ignore_arguments_without_external_programs() {
    for (name, status) in [("true", 0), ("false", 1)] {
        cargo_bin_cmd!()
            .env("PATH", "")
            .args(["-c", &format!("{name} --help --version ignored")])
            .assert()
            .code(status)
            .stdout("")
            .stderr("");
    }
}

#[test]
fn false_controls_conditions_and_pipeline_status() {
    cargo_bin_cmd!()
        .env("PATH", "")
        .args(["-c", "if false; then exit 42; fi; true | false"])
        .assert()
        .code(1)
        .stdout("")
        .stderr("");
}

#[test]
fn in_process_utilities_do_not_initialize_standalone_logging() {
    let directory = assert_fs::TempDir::new().unwrap();
    let logs = directory.child("logs");
    cargo_bin_cmd!()
        .env("PATH", "")
        .env("SANCTUARY_LOG_LEVEL", "info")
        .env("SANCTUARY_LOG_DIR", logs.path())
        .args(["-c", "false; true"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    logs.assert(predicates::path::missing());
}
