fn main() -> std::process::ExitCode {
    cli_log::init("true").finish(i32::from(sanctuary_true::exit_status()))
}
