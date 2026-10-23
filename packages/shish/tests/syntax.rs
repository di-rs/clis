use assert_fs::prelude::*;
use cli_testing::cargo_bin_cmd;

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
