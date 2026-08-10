use assert_cmd::cargo::cargo_bin_cmd;

#[test]
fn runs() {
    let mut cmd = cargo_bin_cmd!("hello");

    cmd.assert()
        .success()
        .stdout(predicates::str::contains("Hello, World!\n"));
}
