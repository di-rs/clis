use clap_verbosity_flag::Verbosity;

pub fn init(verbosity: &Verbosity) {
    env_logger::Builder::new()
        .filter_level(verbosity.log_level_filter())
        .init();
}
