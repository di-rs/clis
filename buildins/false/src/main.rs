fn main() -> std::process::ExitCode {
    cli_log::init("false").finish(i32::from(sanctuary_false::exit_status()))
}
