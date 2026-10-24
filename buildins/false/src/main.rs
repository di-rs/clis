fn main() -> std::process::ExitCode {
    if let Some(status) =
        cli_schema::emit_empty("false", "Exit with status 1; all arguments are ignored")
    {
        return status;
    }
    cli_log::init("false").finish(i32::from(sanctuary_false::exit_status()))
}
