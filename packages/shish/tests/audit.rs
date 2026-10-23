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

#[test]
fn pty_input_backpressure_is_bounded() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut pty = support::pty::Pty::new(
        home.path(),
        &["-c", "stty raw -echo; printf ready; sleep 30"],
        &[],
    )?;
    pty.expect("ready")?;
    let started = std::time::Instant::now();
    let error = pty.send(&"x".repeat(1_048_576)).unwrap_err();
    anyhow::ensure!(error.to_string().contains("PTY write did not complete"));
    drop(pty);
    anyhow::ensure!(started.elapsed() < std::time::Duration::from_secs(5));
    Ok(())
}

#[test]
fn pty_cursor_reply_backpressure_is_bounded() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut pty = support::pty::Pty::new(
        home.path(),
        &[
            "-c",
            "stty raw -echo; while true; do printf '\x1b[6n'; done",
        ],
        &[],
    )?;
    let started = std::time::Instant::now();
    let error = pty.expect("never printed").unwrap_err();
    anyhow::ensure!(
        error.to_string().contains("PTY write did not complete"),
        "{error:#}"
    );
    drop(pty);
    anyhow::ensure!(started.elapsed() < std::time::Duration::from_secs(8));
    Ok(())
}

#[test]
fn bash_extensions_require_explicit_mode() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["--posix", "-c", "command -v declare"])
        .assert()
        .code(1)
        .stdout("")
        .stderr("");
    command(home.path())
        .args([
            "--bash",
            "-c",
            "command -v declare; command -v complete; command -v history",
        ])
        .assert()
        .code(0)
        .stdout("declare\ncomplete\nhistory\n")
        .stderr("");
}

#[test]
fn conflicting_shell_modes_are_rejected() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["--bash", "--posix", "-c", "true"])
        .assert()
        .code(2)
        .stdout("")
        .stderr(predicates::str::contains("cannot be used with"));
}

#[test]
fn bash_parse_only_has_no_effects() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "--bash",
            "-n",
            "-c",
            "values=(one two); printf '%s' \"${values[1]}\" > result",
        ])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    home.child("result").assert(predicates::path::missing());
}

#[test]
fn bash_mode_keeps_sanctuary_status_registrations() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["--bash", "-c", "help true; help false"])
        .assert().code(0).stdout("true: Sanctuary builtin; ignore arguments and return 0.\nfalse: Sanctuary builtin; ignore arguments and return 1.\n").stderr("");
}

#[test]
fn task_01_prompt_tracks_directory_and_ps1() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    home.child("nested").create_dir_all()?;
    let mut terminal =
        support::pty::Pty::with_prompt(&home.path().canonicalize()?, &[], &[], None)?;
    terminal.expect("~ > ")?;
    terminal.send("cd nested\n")?;
    terminal.expect("nested > ")?;
    terminal.send("PS1='custom> '\n")?;
    terminal.expect("\ncustom> ")?;
    terminal.send("exit\n")?;
    terminal.finish(0)?;
    command(home.path())
        .env("PS1", "must-not-print> ")
        .args(["-c", "printf output"])
        .assert()
        .code(0)
        .stdout("output")
        .stderr("");
    Ok(())
}

#[test]
fn task_02_invalid_command_preserves_repl_execution() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .arg("-s")
        .write_stdin("shish_missing_command\nprintf '%s\\n' \"$?\"\n")
        .assert()
        .code(0)
        .stdout("127\n")
        .stderr("error: command not found: shish_missing_command\n");
}

#[test]
fn task_03_repl_accepts_multiline_commands_and_keeps_state() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("for value in one two; do\n")?;
    terminal.expect("more> ")?;
    terminal.send(" /bin/echo \"$value\"\ndone\n")?;
    terminal.expect("\none\ntwo\n")?;
    terminal.send("printf '<%s>\\n' \"$value\"\n")?;
    terminal.expect("\n<two>\n")?;
    terminal.send("exit\n")?;
    terminal.finish(0)
}

#[test]
fn task_04_exit_stops_execution_and_preserves_status() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "exit 23; printf wrong"])
        .assert()
        .code(23)
        .stdout("")
        .stderr("");
    command(home.path())
        .args(["-c", "false; exit"])
        .assert()
        .code(1)
        .stdout("")
        .stderr("");
}

#[test]
fn task_05_echo_preserves_argument_boundaries() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "echo one '' three; echo"])
        .assert()
        .code(0)
        .stdout("one  three\n\n")
        .stderr("");
    command(home.path())
        .env("PATH", "")
        .args(["--bash", "-c", "echo one '' three; echo"])
        .assert()
        .code(0)
        .stdout("one  three\n\n")
        .stderr("");
}

#[test]
fn task_06_type_classifies_commands() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "type true; type /bin/echo; f() { :; }; type -t f"])
        .assert()
        .code(0)
        .stdout("true is a shell builtin\n/bin/echo is /bin/echo\nfunction\n")
        .stderr("");
    command(home.path())
        .args(["-c", "type shish_missing_type"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("shish_missing_type"));
}

#[test]
#[ignore = "P06: command -v does not report shell functions"]
fn posix_command_v_reports_functions() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "f() { :; }; command -v f"])
        .assert()
        .code(0)
        .stdout("f\n")
        .stderr("");
}

#[test]
fn task_07_path_finds_executable_without_build_directory_discovery() {
    use std::os::unix::fs::PermissionsExt as _;
    let home = assert_fs::TempDir::new().unwrap();
    let tool = home.child("shish_lookup_fixture");
    tool.write_str("#!/bin/sh\nprintf 'found:%s\\n' \"$1\"\n")
        .unwrap();
    std::fs::set_permissions(tool.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    command(home.path())
        .env("PATH", home.path())
        .args(["-c", "shish_lookup_fixture 'a b'"])
        .assert()
        .code(0)
        .stdout("found:a b\n")
        .stderr("");
    command(home.path())
        .env("PATH", home.path().join("absent"))
        .args(["-c", "shish_lookup_fixture"])
        .assert()
        .code(127)
        .stdout("")
        .stderr("error: command not found: shish_lookup_fixture\n");
}

#[test]
fn task_08_external_program_preserves_arguments_streams_and_status() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "/bin/sh -c 'printf \"<%s>\\n\" \"$1\"; printf diagnostic >&2; exit 19' name 'a b'",
        ])
        .assert()
        .code(19)
        .stdout("<a b>\n")
        .stderr("diagnostic");
}

#[test]
fn task_09_pwd_reflects_shell_directory() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("nested").create_dir_all().unwrap();
    let root = home.path().canonicalize().unwrap();
    let expected = format!("{}\n{}\n", root.display(), root.join("nested").display());
    command(&root)
        .args(["-c", "pwd; cd nested; pwd"])
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
}

#[test]
fn task_10_cd_accepts_absolute_paths_with_spaces() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("target space").create_dir_all().unwrap();
    let target = home.child("target space").path().canonicalize().unwrap();
    command(home.path())
        .args(["-c", "cd \"$1\"; pwd", "shish"])
        .arg(&target)
        .assert()
        .code(0)
        .stdout(format!("{}\n", target.display()))
        .stderr("");
}

#[test]
fn task_11_cd_resolves_relative_paths_and_parent_components() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("one/two").create_dir_all().unwrap();
    let root = home.path().canonicalize().unwrap();
    command(&root)
        .args(["-c", "cd one/two; cd ..; pwd"])
        .assert()
        .code(0)
        .stdout(format!("{}\n", root.join("one").display()))
        .stderr("");
}

#[test]
fn task_12_cd_without_operand_and_tilde_use_home() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("nested").create_dir_all().unwrap();
    let root = home.path().canonicalize().unwrap();
    command(&root)
        .args(["-c", "cd nested; cd; pwd; cd nested; cd ~; pwd"])
        .assert()
        .code(0)
        .stdout(format!("{}\n{}\n", root.display(), root.display()))
        .stderr("");
}

#[test]
fn task_13_single_quotes() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "printf '<%s>\\n' 'a b' '' '$HOME \\ * ; &' 'a'\"'\"'b'",
        ])
        .assert()
        .code(0)
        .stdout("<a b>\n<>\n<$HOME \\ * ; &>\n<a'b>\n")
        .stderr("");
}

#[test]
fn task_14_double_quotes() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("matching-file").touch().unwrap();
    command(home.path())
        .args([
            "-c",
            "v='a b'; empty=; printf '<%s>\\n' \"$v\" \"$empty\" \"*\" \"pre${v}post\"",
        ])
        .assert()
        .code(0)
        .stdout("<a b>\n<>\n<*>\n<prea bpost>\n")
        .stderr("");
}

#[test]
fn task_15_unquoted_backslashes() {
    let home = assert_fs::TempDir::new().unwrap();
    home.child("matching-file").touch().unwrap();
    command(home.path())
        .args(["-c", "printf '<%s>\\n' a\\ b \\$HOME \\* a\\\nb"])
        .assert()
        .code(0)
        .stdout("<a b>\n<$HOME>\n<*>\n<ab>\n")
        .stderr("");
}

#[test]
fn task_16_single_quote_backslashes() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "printf '<%s>\\n' 'a\\b' '\\$HOME' '\\\\' '\\n'"])
        .assert()
        .code(0)
        .stdout("<a\\b>\n<\\$HOME>\n<\\\\>\n<\\n>\n")
        .stderr("");
}

#[test]
fn task_17_double_quote_backslashes() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "v=ok; printf '<%s>\\n' \"\\\\$v\" \"\\$v\" \"\\q\" \"\\\"\" \"a\\\nb\"",
        ])
        .assert()
        .code(0)
        .stdout("<\\ok>\n<$v>\n<\\q>\n<\">\n<ab>\n")
        .stderr("");
}

#[test]
fn task_18_quoted_executable_path() {
    use std::os::unix::fs::PermissionsExt as _;
    let home = assert_fs::TempDir::new().unwrap();
    let tool = home.child("tool with space");
    tool.write_str("#!/bin/sh\nprintf '<%s>\\n' \"$1\"\n")
        .unwrap();
    std::fs::set_permissions(tool.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
    for script in ["'./tool with space' 'a b'", "\"./tool with space\" 'a b'"] {
        command(home.path())
            .args(["-c", script])
            .assert()
            .code(0)
            .stdout("<a b>\n")
            .stderr("");
    }
}

#[test]
fn task_19_stdout_redirection() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "printf first-longer-value > out; printf second > out; printf visible",
        ])
        .assert()
        .code(0)
        .stdout("visible")
        .stderr("");
    home.child("out").assert("second");
}

#[test]
fn task_20_stderr_redirection() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["-c", "/bin/sh -c 'printf visible; printf first-longer-value >&2' 2> err; /bin/sh -c 'printf second >&2' 2> err"])
        .assert().code(0).stdout("visible").stderr("");
    home.child("err").assert("second");
}

#[test]
fn task_21_stdout_append() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "printf first > out; printf second >> out; printf new >> created; printf visible",
        ])
        .assert()
        .code(0)
        .stdout("visible")
        .stderr("");
    home.child("out").assert("firstsecond");
    home.child("created").assert("new");
}

#[test]
fn task_22_stderr_append() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["-c", "/bin/sh -c 'printf first >&2' 2> err; /bin/sh -c 'printf visible; printf second >&2' 2>> err; /bin/sh -c 'printf new >&2' 2>> created"])
        .assert().code(0).stdout("visible").stderr("");
    home.child("err").assert("firstsecond");
    home.child("created").assert("new");
}

#[test]
fn task_55_two_command_pipeline() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "/bin/sh -c 'printf abc; exit 7' | tr a-z A-Z"])
        .assert()
        .code(0)
        .stdout("ABC")
        .stderr("");
}

#[test]
fn task_56_builtin_pipeline() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["-c", "false | true; printf '%s\\n' \"$?\"; true | false; printf '%s\\n' \"$?\"; v=parent; printf child | read v; printf '%s\\n' \"$v\""])
        .assert().code(0).stdout("0\n1\nparent\n").stderr("");
}

#[test]
fn task_57_multi_command_pipeline() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "printf 'alpha\\nbeta\\n' | tr a-z A-Z | sed -n '2p'"])
        .assert()
        .code(0)
        .stdout("BETA\n")
        .stderr("");
}

#[test]
fn task_70_declare() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "--bash",
            "-c",
            "declare answer='a b'; printf '<%s>\\n' \"$answer\"",
        ])
        .assert()
        .code(0)
        .stdout("<a b>\n")
        .stderr("");
}

#[test]
fn task_71_missing_variable() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["--bash", "-c", "declare -p shish_missing_variable"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("shish_missing_variable"));
}

#[test]
fn task_72_variable_storage() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["-c", "v='a b'; printf '<%s>\\n' \"$v\"; export v; /bin/sh -c 'printf \"<%s>\\n\" \"$v\"; v=child'; printf '<%s>\\n' \"$v\""])
        .assert().code(0).stdout("<a b>\n<a b>\n<a b>\n").stderr("");
}

#[test]
fn task_73_variable_names() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["--bash", "-c", "declare 'bad-name=value'"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicates::str::contains("bad-name"));
}

#[test]
fn task_74_variable_expansion() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args(["-c", "v='a b'; printf '<%s>\\n' $v \"$v\""])
        .assert()
        .code(0)
        .stdout("<a>\n<b>\n<a b>\n")
        .stderr("");
}

#[test]
#[expect(
    clippy::literal_string_with_formatting_args,
    reason = "Shell parameter operators use braces, not Rust formatting"
)]
fn task_75_braced_expansion() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path())
        .args([
            "-c",
            "v=value; printf '%s/%s\\n' \"${v}_tail\" \"${shish_unset:-fallback}\"",
        ])
        .assert()
        .code(0)
        .stdout("value_tail/fallback\n")
        .stderr("");
}

#[test]
fn task_76_empty_expansion() {
    let home = assert_fs::TempDir::new().unwrap();
    command(home.path()).args(["-c", "unset v; empty=; set -- $v \"$v\" $empty \"$empty\"; printf '%s\\n' \"$#\"; printf '<%s>\\n' \"$@\""])
        .assert().code(0).stdout("2\n<>\n<>\n").stderr("");
}

#[test]
fn task_23_builtin_completion() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal =
        support::pty::Pty::new(home.path(), &["-i"], &[("PATH", "/shish-no-tools")])?;
    terminal.expect("shish-test> ")?;
    terminal.send("fals\t")?;
    terminal.expect("shish-test> false ")?;
    terminal.send("\n")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit $?\n")?;
    terminal.finish(1)
}

#[test]
fn task_24_completion_arguments() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal =
        support::pty::Pty::new(home.path(), &["-i"], &[("PATH", "/shish-no-tools")])?;
    terminal.expect("shish-test> ")?;
    terminal.send("exi\t")?;
    terminal.expect("shish-test> exit ")?;
    terminal.send("7\n")?;
    terminal.finish(7)
}

#[test]
fn task_26_executable_completion() -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt as _;
    let home = assert_fs::TempDir::new()?;
    let tool = home.child("shish_fixture_command");
    tool.write_str("#!/bin/sh\nexit 23\n")?;
    std::fs::set_permissions(tool.path(), std::fs::Permissions::from_mode(0o755))?;
    let path = home.path().to_str().unwrap();
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[("PATH", path)])?;
    terminal.expect("shish-test> ")?;
    terminal.send("shish_fixture_c\t")?;
    terminal.expect("shish-test> shish_fixture_command ")?;
    terminal.send("\n")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit $?\n")?;
    terminal.finish(23)
}

#[test]
fn task_27_multiple_command_completions() -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt as _;
    let home = assert_fs::TempDir::new()?;
    for name in ["shish_pick_one", "shish_pick_two"] {
        let tool = home.child(name);
        tool.write_str("#!/bin/sh\nexit 0\n")?;
        std::fs::set_permissions(tool.path(), std::fs::Permissions::from_mode(0o755))?;
    }
    let mut terminal = support::pty::Pty::new(
        home.path(),
        &["-i"],
        &[("PATH", home.path().to_str().unwrap())],
    )?;
    terminal.expect("shish-test> ")?;
    terminal.send("shish_pick_\t")?;
    terminal.expect("shish_pick_one  shish_pick_two")?;
    terminal.send("\x03")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
fn task_29_file_completion() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    home.child("unique file")
        .write_str("file-completion-ok\n")?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/cat uniq\t")?;
    terminal.expect("shish-test> /bin/cat unique\\ file ")?;
    terminal.send("\n")?;
    terminal.expect("\nfile-completion-ok\n")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
fn task_30_nested_file_completion() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    home.child("nested/deep").create_dir_all()?;
    home.child("nested/deep/file.txt")
        .write_str("nested-completion-ok\n")?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/cat nested/de\t")?;
    terminal.expect("shish-test> /bin/cat nested/deep/")?;
    terminal.send("fi\t")?;
    terminal.expect("shish-test> /bin/cat nested/deep/file.txt ")?;
    terminal.send("\n")?;
    terminal.expect("\nnested-completion-ok\n")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
fn task_31_directory_completion() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    home.child("unique_directory").create_dir_all()?;
    let root = home.path().canonicalize()?;
    let mut terminal = support::pty::Pty::new(&root, &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("cd uniq\t")?;
    terminal.expect("shish-test> cd unique_directory/")?;
    terminal.send("\n")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("/bin/pwd\n")?;
    terminal.expect(&format!("\n{}\n", root.join("unique_directory").display()))?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
fn task_33_multiple_file_completions() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    home.child("pick_one").touch()?;
    home.child("pick_two").touch()?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/cat pick_\t")?;
    terminal.expect("pick_one")?;
    terminal.expect("pick_two")?;
    terminal.send("\x03")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
fn task_35_multiple_argument_completion() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    home.child("first.txt").write_str("first-content\n")?;
    home.child("second.txt").write_str("second-content\n")?;
    let mut terminal = support::pty::Pty::new(home.path(), &["-i"], &[])?;
    terminal.expect("shish-test> ")?;
    terminal.send("/bin/cat fir\t")?;
    terminal.expect("shish-test> /bin/cat first.txt ")?;
    terminal.send("sec\t")?;
    terminal.expect("shish-test> /bin/cat first.txt second.txt ")?;
    terminal.send("\n")?;
    terminal.expect("\nfirst-content\nsecond-content\n")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
fn task_25_unmatched_input_can_be_cancelled() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal =
        support::pty::Pty::new(home.path(), &["-i"], &[("PATH", "/shish-no-tools")])?;
    terminal.expect("shish-test> ")?;
    terminal.send("shish_missing_completion\t_suffix")?;
    terminal.expect("shish-test> shish_missing_completion_suffix")?;
    terminal.send("\x03")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}

#[test]
#[ignore = "C25: an empty Reedline completion menu consumes the first Enter"]
fn task_25_enter_executes_after_no_completion() -> anyhow::Result<()> {
    let home = assert_fs::TempDir::new()?;
    let mut terminal =
        support::pty::Pty::new(home.path(), &["-i"], &[("PATH", "/shish-no-tools")])?;
    terminal.expect("shish-test> ")?;
    terminal.send("shish_missing_completion\t")?;
    terminal.expect("shish-test> shish_missing_completion")?;
    terminal.send("\n")?;
    terminal.expect("error: command not found: shish_missing_completion")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit $?\n")?;
    terminal.finish(127)
}

#[test]
#[ignore = "C28: Brush does not enable Reedline partial completions"]
fn task_28_command_common_prefix() -> anyhow::Result<()> {
    use std::os::unix::fs::PermissionsExt as _;
    let home = assert_fs::TempDir::new()?;
    for name in ["shish_pick_one", "shish_pick_two"] {
        let tool = home.child(name);
        tool.write_str("#!/bin/sh\nexit 0\n")?;
        std::fs::set_permissions(tool.path(), std::fs::Permissions::from_mode(0o755))?;
    }
    let mut terminal = support::pty::Pty::new(
        home.path(),
        &["-i"],
        &[("PATH", home.path().to_str().unwrap())],
    )?;
    terminal.expect("shish-test> ")?;
    terminal.send("shish_p\t")?;
    terminal.expect_screen_line("shish-test> shish_pick_")?;
    terminal.send("\x03")?;
    terminal.expect_line("shish-test> ")?;
    terminal.send("exit 0\n")?;
    terminal.finish(0)
}
