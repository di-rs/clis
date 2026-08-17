use clap_verbosity_flag::{ErrorLevel, LogLevel, VerbosityFilter};

// We are making copy of verbosity module to remove `-v` frop verbose
// This flag is reserved for `grepr` cli implementation

/// Logging flags to `#[command(flatten)]` into your CLI
#[derive(clap::Args, Debug, Clone, Copy, Default, PartialEq, Eq)]
#[command(about = None, long_about = None)]
pub struct Verbosity<L: LogLevel = ErrorLevel> {
    #[arg(
        long,
        action = clap::ArgAction::Count,
        global = true,
        help = L::verbose_help(),
        long_help = L::verbose_long_help(),
    )]
    verbose: u8,

    #[arg(
        long,
        short = 'q',
        action = clap::ArgAction::Count,
        global = true,
        help = L::quiet_help(),
        long_help = L::quiet_long_help(),
        conflicts_with = "verbose",
    )]
    quiet: u8,

    #[arg(skip)]
    phantom: std::marker::PhantomData<L>,
}

#[allow(
    clippy::cast_lossless,
    clippy::as_conversions,
    clippy::arithmetic_side_effects
)]
impl<L: LogLevel> Verbosity<L> {
    /// Gets the filter that should be applied to the logger.
    pub fn filter(&self) -> VerbosityFilter {
        let offset = self.verbose as i16 - self.quiet as i16;
        with_offset(L::default_filter(), offset)
    }
}

impl<L: LogLevel> Verbosity<L> {
    /// Get the log level filter.
    pub fn log_level_filter(&self) -> log::LevelFilter {
        self.filter().into()
    }
}

fn with_offset(filter: VerbosityFilter, offset: i16) -> VerbosityFilter {
    match i16::from(value(filter)).saturating_add(offset) {
        i16::MIN..=0 => VerbosityFilter::Off,
        1 => VerbosityFilter::Error,
        2 => VerbosityFilter::Warn,
        3 => VerbosityFilter::Info,
        4 => VerbosityFilter::Debug,
        5..=i16::MAX => VerbosityFilter::Trace,
    }
}

const fn value(filter: VerbosityFilter) -> u8 {
    match filter {
        VerbosityFilter::Off => 0,
        VerbosityFilter::Error => 1,
        VerbosityFilter::Warn => 2,
        VerbosityFilter::Info => 3,
        VerbosityFilter::Debug => 4,
        VerbosityFilter::Trace => 5,
    }
}
