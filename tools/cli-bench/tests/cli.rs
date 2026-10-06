use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;

#[test]
fn help_identifies_the_interface_on_stdout() {
    for flag in ["-h", "--help"] {
        cargo_bin_cmd!()
            .env_remove("CLIS_LOG_LEVEL")
            .arg(flag)
            .assert()
            .success()
            .stderr("")
            .stdout(predicate::str::contains(
                "Correctness-checked CLI benchmark evidence",
            ))
            .stdout(predicate::str::contains("Usage: cli-bench"))
            .stdout(predicate::str::contains("--log-level"));
    }
}

#[test]
fn version_identifies_the_package_on_stdout() {
    for flag in ["-V", "--version"] {
        let assertion = cargo_bin_cmd!()
            .env_remove("CLIS_LOG_LEVEL")
            .arg(flag)
            .assert()
            .success()
            .stderr("");
        assert_eq!(assertion.get_output().stdout, b"cli-bench 0.1.0\n");
    }
}

#[test]
fn no_arguments_shows_the_available_interface() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .assert()
        .success()
        .stderr("")
        .stdout(predicate::str::contains("Usage: cli-bench"));
}

#[test]
fn invalid_logging_returns_cli_input_status() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .args(["-L", "invalid"])
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicate::str::contains("invalid --log-level"));
}

#[test]
fn unknown_arguments_return_cli_input_status() {
    cargo_bin_cmd!()
        .env_remove("CLIS_LOG_LEVEL")
        .arg("--unknown")
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicate::str::contains("unexpected argument"));
}

#[test]
fn explicit_logging_overrides_an_invalid_environment() {
    cargo_bin_cmd!()
        .env("CLIS_LOG_LEVEL", "invalid")
        .args(["-L", "off"])
        .assert()
        .success()
        .stderr("")
        .stdout(predicate::str::contains("Usage: cli-bench"));
}
