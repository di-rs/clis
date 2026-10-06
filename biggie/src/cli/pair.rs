use super::EndingArgs;
use biggie::PairOptions;
use clap::Args;
use std::{io, path::PathBuf};
#[derive(Args, Debug)]
pub struct PairArgs {
    /// Left destination (named file required)
    #[arg(short = 'l', long)]
    pub left: PathBuf,
    /// Right destination (named file required)
    #[arg(short = 'r', long)]
    pub right: PathBuf,
    /// Distinct keys unique to the left
    #[arg(short = 'a', long)]
    pub left_only: u64,
    /// Distinct keys common to both streams
    #[arg(short = 'j', long)]
    pub shared: u64,
    /// Distinct keys unique to the right
    #[arg(short = 'b', long)]
    pub right_only: u64,
    /// Multiplicity of every key
    #[arg(short='c',long,default_value="1",value_parser=clap::value_parser!(u64).range(1..))]
    pub copies: u64,
    /// Seed for a reproducible random key prefix
    #[arg(short = 's', long)]
    pub seed: Option<u64>,
    #[command(flatten)]
    pub ending: EndingArgs,
}
impl PairArgs {
    pub fn options(&self) -> PairOptions {
        PairOptions {
            left_only: self.left_only,
            shared: self.shared,
            right_only: self.right_only,
            copies: self.copies,
            seed: self.seed,
            line_ending: self.ending.line_ending.into(),
            final_newline: !self.ending.no_final_newline,
        }
    }
    pub fn validate(&self) -> io::Result<()> {
        if self.left.as_os_str() == "-" || self.right.as_os_str() == "-" {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "pair requires two named file destinations",
            ));
        }
        self.options().validate()
    }
}
