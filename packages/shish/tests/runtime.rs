use assert_fs::prelude::*;
use cli_testing::cargo_bin_cmd;

#[test]
fn command_arguments_have_shell_positional_semantics() {
    cargo_bin_cmd!()
        .args([
            "-c",
            "printf '%s:%s:%s\\n' \"$0\" \"$1\" \"$#\"",
            "name",
            "one",
        ])
        .assert()
        .code(0)
        .stdout("name:one:1\n")
        .stderr("");
}

#[test]
fn script_arguments_and_exit_status() {
    let directory = assert_fs::TempDir::new().unwrap();
    directory
        .child("test.sh")
        .write_str("printf '%s:%s\\n' \"$0\" \"$1\"; exit 7\n")
        .unwrap();
    cargo_bin_cmd!()
        .current_dir(&directory)
        .args(["test.sh", "argument"])
        .assert()
        .code(7)
        .stdout("test.sh:argument\n")
        .stderr("");
}

#[test]
fn stdin_supports_multiline_programs_and_parameters() {
    cargo_bin_cmd!()
        .args(["-s", "--", "argument"])
        .write_stdin("for value in \"$1\" two; do\n printf '%s\\n' \"$value\"\ndone\n")
        .assert()
        .code(0)
        .stdout("argument\ntwo\n")
        .stderr("");
}

#[test]
fn read_consumes_only_its_input_from_script_stdin() {
    cargo_bin_cmd!()
        .arg("-s")
        .write_stdin("read value\ninput data\nprintf '<%s>\\n' \"$value\"\n")
        .assert()
        .code(0)
        .stdout("<input data>\n")
        .stderr("");
}

#[test]
fn external_program_can_consume_remaining_stdin() {
    cargo_bin_cmd!()
        .arg("-s")
        .write_stdin("/bin/cat\npayload\n")
        .assert()
        .code(0)
        .stdout("payload\n")
        .stderr("");
}

#[test]
fn false_does_not_terminate_execution() {
    cargo_bin_cmd!()
        .args(["--posix", "-c", "false; printf '%s\\n' \"$?\"; true"])
        .assert()
        .code(0)
        .stdout("1\n")
        .stderr("");
}

#[test]
fn stdin_without_final_newline_preserves_last_status() {
    cargo_bin_cmd!()
        .write_stdin("printf final; false")
        .assert()
        .code(1)
        .stdout("final")
        .stderr("");
}

#[test]
fn syntax_check_accepts_script_path_without_side_effects() {
    let directory = assert_fs::TempDir::new().unwrap();
    directory
        .child("test.sh")
        .write_str("printf changed > created\n")
        .unwrap();
    cargo_bin_cmd!()
        .current_dir(&directory)
        .args(["-n", "test.sh"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    directory
        .child("created")
        .assert(predicates::path::missing());
}

#[test]
fn startup_does_not_source_inherited_configuration() {
    let directory = assert_fs::TempDir::new().unwrap();
    let startup = directory.child("startup");
    startup.write_str("printf unexpected; exit 42\n").unwrap();
    cargo_bin_cmd!()
        .env("HOME", directory.path())
        .env("ENV", startup.path())
        .env("BASH_ENV", startup.path())
        .args(["-c", "printf expected"])
        .assert()
        .code(0)
        .stdout("expected")
        .stderr("");
}
