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
