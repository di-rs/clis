mod support;

use assert_fs::prelude::*;
use predicates::prelude::PredicateBooleanExt as _;
use support::command;

#[test]
fn runtime_syntax_error_has_status_two_and_no_effects() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "printf before; )"])
        .assert()
        .code(2)
        .stdout("")
        .stderr("error: -c: syntax error at line 1 col 16\n");
}

#[test]
fn nonexecutable_command_has_status_126() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("nonexec")
        .write_str("echo should-not-run\n")
        .unwrap();
    command(home.path())
        .args(["-c", "./nonexec"])
        .assert()
        .code(126)
        .stdout("")
        .stderr("error: failed to execute command './nonexec': Permission denied (os error 13)\n");
}

#[test]
fn failed_redirection_does_not_run_the_command() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "printf text > absent/out"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("failed to redirect to").and(
            predicates::str::ends_with("/absent/out: No such file or directory (os error 2)\n"),
        ));
    home.child("absent").assert(predicates::path::missing());
}

#[test]
fn heredoc_expansion_and_quoted_delimiter() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "v=value; cat <<EOF\n$v\nEOF\ncat <<'EOF'\n$v\nEOF\n"])
        .assert()
        .code(0)
        .stdout("value\n$v\n")
        .stderr("");
}

#[test]
fn functions_control_flow_and_subshell_state() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["-c", "f() { for x in a b; do case $x in a) continue;; b) printf '<%s>\\n' \"$1\";; esac; done; }; v=outer; (v=inner); f \"$v\""])
        .assert().code(0).stdout("<outer>\n").stderr("");
}

#[test]
fn exit_trap_preserves_explicit_exit_status() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "trap 'printf trapped' EXIT; exit 7"])
        .assert()
        .code(7)
        .stdout("trapped")
        .stderr("");
}

#[test]
fn pty_session_accepts_input_and_eof() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/echo pty-ready\n")?;
    terminal.expect("\npty-ready\n")?;
    terminal.send("\x04")?;
    terminal.finish(0)
}

// These acceptance cases intentionally remain ignored until the corresponding
// independently tracked POSIX gap is fixed. They assert the required result,
// never the current defect. See docs/compatibility.md.
#[test]
#[ignore = "P01: Brush loses empty non-whitespace IFS fields"]
fn posix_ifs_preserves_empty_fields() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "IFS=:; v=':a::b:'; set -- $v; printf '<%s>\\n' \"$@\"",
        ])
        .assert()
        .code(0)
        .stdout("<>\n<a>\n<>\n<b>\n")
        .stderr("");
}

#[test]
#[ignore = "P02: Brush discards assignments before special builtins"]
fn posix_special_builtin_assignment_persists() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "unset v; v=kept :; printf '<%s>\\n' \"$v\""])
        .assert()
        .code(0)
        .stdout("<kept>\n")
        .stderr("");
}

#[test]
#[ignore = "P03: Brush does not populate the asynchronous process parameter"]
fn posix_background_process_parameter_is_set() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "/bin/sleep 0.01 & printf '<%s>\\n' \"${!:+set}\"; wait",
        ])
        .assert()
        .code(0)
        .stdout("<set>\n")
        .stderr("");
}

#[test]
fn pty_guard_cleans_up_descendants() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/sh -c 'echo $$ > worker.pid; echo worker-ready; sleep 30'\n")?;
    terminal.expect("\nworker-ready")?;
    let pid = std::fs::read_to_string(home.child("worker.pid").path())?
        .trim()
        .parse::<u32>()?;
    drop(terminal);
    let pid = sysinfo::Pid::from_u32(pid);
    let started = std::time::Instant::now();
    loop {
        let mut system = sysinfo::System::new();
        system.refresh_processes(sysinfo::ProcessesToUpdate::Some(&[pid]), true);
        if system
            .process(pid)
            .is_none_or(|process| process.status() == sysinfo::ProcessStatus::Zombie)
        {
            break;
        }
        anyhow::ensure!(
            started.elapsed() < std::time::Duration::from_secs(2),
            "PTY descendant survived cleanup"
        );
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    Ok(())
}

#[test]
#[ignore = "P04: TERM trap does not prevent default signal termination"]
fn posix_term_trap_handles_signal() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "trap 'printf caught' TERM; kill -TERM $$; printf done",
        ])
        .assert()
        .code(0)
        .stdout("caughtdone")
        .stderr("");
}

#[test]
fn foreground_interrupt_returns_control_to_shell() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/sh -c 'echo foreground-ready; sleep 30'\n")?;
    terminal.expect("\nforeground-ready")?;
    terminal.send("\x03")?;
    terminal.send("exit $?\n")?;
    terminal.finish(130)
}

#[test]
fn startup_policy_skips_interactive_env() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let startup = home.child("startup");
    startup.write_str("export MARK=loaded\n")?;
    let path = startup.path().to_str().unwrap();
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[("ENV", path)])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/usr/bin/printf '<%s>\\n' \"$MARK\"\n")?;
    terminal.expect("\n<>\n")?;
    terminal.send("exit\n")?;
    terminal.finish(0)
}
