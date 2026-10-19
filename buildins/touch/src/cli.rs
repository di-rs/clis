use clap::{ArgAction, Parser};
use rustix::fs::Timespec;
use std::path::PathBuf;
use touch::{parse_adjustment, parse_timestamp};

/// Change file access and modification times, creating missing files.
#[derive(Parser, Debug)]
#[allow(
    clippy::struct_excessive_bools,
    reason = "The booleans represent independent CLI switches."
)]
#[command(author, version, disable_help_flag = true, args_override_self = true)]
pub struct Cli {
    /// Change only the access time (combine with -m for both)
    #[arg(short = 'a')]
    pub access: bool,
    /// Change only the modification time (combine with -a for both)
    #[arg(short = 'm')]
    pub modify: bool,
    /// Do not create missing files
    #[arg(short = 'c', long = "no-create")]
    pub no_create: bool,
    /// Change symbolic links themselves; implies -c
    #[arg(short = 'h', long = "no-dereference")]
    pub no_dereference: bool,
    /// Accepted for compatibility; ignored
    #[arg(short = 'f')]
    pub force: bool,
    /// Set local time: `[[CC]YY]MMDDhhmm[.SS]`
    #[arg(short = 't', value_parser = parse_timestamp, overrides_with_all = ["date", "reference"])]
    pub timestamp: Option<Timespec>,
    /// Use a BSD timestamp or GNU date expression
    #[arg(short = 'd', long, overrides_with = "timestamp")]
    pub date: Option<String>,
    /// Copy this file's times; -d can offset each time
    #[arg(short = 'r', long, overrides_with = "timestamp", value_name = "FILE")]
    pub reference: Option<PathBuf>,
    /// Adjust times by [-][[hh]mm]SS; implies -c
    #[arg(short = 'A', value_parser = parse_adjustment, allow_hyphen_values = true)]
    pub adjustment: Option<i64>,
    /// Select access (access, atime, use) or modification (modify, mtime) time
    #[arg(long = "time", value_parser = ["access", "atime", "use", "modify", "mtime"])]
    pub time: Option<String>,
    /// Print help
    #[arg(long, action = ArgAction::Help)]
    pub help: Option<bool>,
    /// Files to update; '-' is a literal filename
    #[arg(required = true)]
    pub files: Vec<PathBuf>,
}
