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
