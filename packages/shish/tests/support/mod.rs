pub mod pty;

/// Build an isolated CLI command; the caller keeps the temporary home alive.
pub fn command(home: &std::path::Path) -> cli_testing::Command {
    let mut command = cli_testing::cargo_bin_cmd!();
    command
        .env_clear()
        .envs([
            ("PATH", "/usr/bin:/bin"),
            ("LC_ALL", "C"),
            ("TZ", "UTC"),
            ("TERM", "dumb"),
            ("NO_COLOR", "1"),
            ("SANCTUARY_LOG_LEVEL", "off"),
        ])
        .env("HOME", home)
        .current_dir(home);
    command
}
