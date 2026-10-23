fn main() -> std::process::ExitCode {
    if let Some(status) =
        cli_schema::emit_empty("true", "Exit with status 0; all arguments are ignored")
    {
        return status;
    }
    cli_log::init("true").finish(i32::from(sanctuary_true::exit_status()))
}
