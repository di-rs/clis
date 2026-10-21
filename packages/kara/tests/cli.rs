#[cfg(unix)]
#[test]
fn startup_without_a_controlling_terminal_reports_an_error() {
    // Crossterm can use /dev/tty even with piped stdin. Leave interactive terminal
    // checks to PTY/manual verification; this scenario with a TTY could hang.
    if std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty")
        .is_ok()
    {
        eprintln!("skipping non-TTY startup check: a controlling terminal is attached");
        return;
    }

    cli_testing::cargo_bin_cmd!()
        .write_stdin("")
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("os error"));
}

#[test]
fn discovery_does_not_need_a_terminal() {
    for flag in ["--help", "--version"] {
        cli_testing::cargo_bin_cmd!()
            .arg(flag)
            .assert()
            .code(0)
            .stdout(predicates::str::contains("kara"))
            .stderr("");
    }
    cli_testing::cargo_bin_cmd!()
        .args(["one", "two"])
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicates::str::contains("unexpected argument"));
}
