use thiserror::Error;

#[derive(Error, Debug)]
pub enum CliError {
    #[error("incorrect cli config passed")]
    Config,
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}
