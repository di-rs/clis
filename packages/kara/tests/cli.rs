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

#[cfg(unix)]
#[test]
fn filenames_cannot_inject_terminal_commands() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/terminal.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .env("CLI_LOG", "off")
        .output()
        .expect("run PTY regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn saves_preserve_permissions_and_editor_state() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/pty_smoke.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY save regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn modal_editing_preserves_keybindings_and_saved_bytes() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/interaction.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY interaction regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn editing_respects_grapheme_and_document_boundaries() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/editing.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY editing regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn text_io_preserves_utf8_bytes_and_line_endings() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/text_io.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY text I/O regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn search_preserves_modal_interaction() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/search.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY search regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

#[cfg(unix)]
#[test]
fn syntax_and_file_type_follow_saved_identity() {
    let result = std::process::Command::new("python3")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/syntax.py"))
        .arg(env!("CARGO_BIN_EXE_kara"))
        .output()
        .expect("run PTY syntax regression driver");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}
