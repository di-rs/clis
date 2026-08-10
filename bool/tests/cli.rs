use assert_cmd::cargo::cargo_bin_cmd;

#[test]
fn false_not_ok() {
    let mut cmd = cargo_bin_cmd!("false");
    cmd.assert().failure();
}
