use clap_verbosity_flag::Verbosity;
use color_eyre::eyre::Result;

/// # Errors
/// Throws an error if couldn't initialize `coloe_eyre`
pub fn init(verbosity: &Verbosity) -> Result<()> {
    if std::env::var_os("RUST_BACKTRACE").is_none() {
        match verbosity.log_level() {
            Some(log::Level::Info) => unsafe {
                // SAFETY: Called during single-threaded application initialization,
                // before any application threads are spawned.
                std::env::set_var("RUST_BACKTRACE", "1");
            },
            Some(log::Level::Debug | log::Level::Trace) => unsafe {
                // SAFETY: Called during single-threaded application initialization,
                // before any application threads are spawned.
                std::env::set_var("RUST_BACKTRACE", "full");
            },
            _ => {}
        }
    }

    color_eyre::install()
}
