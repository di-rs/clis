/// Stable classification for callers deciding how to report a suite failure.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ErrorKind {
    UnsupportedSchema,
    InvalidSuite,
}

/// A suite configuration failure; process status belongs to the CLI adapter.
#[derive(Debug, thiserror::Error)]
pub enum BenchError {
    #[error("unsupported suite schema version {0}; expected 1")]
    UnsupportedSchema(u32),
    #[error("invalid suite: {0}")]
    InvalidSuite(String),
    #[error("invalid suite TOML: {0}")]
    Parse(#[from] toml::de::Error),
}

impl BenchError {
    /// Classify a failure without inspecting diagnostic wording.
    #[must_use]
    pub const fn kind(&self) -> ErrorKind {
        match self {
            Self::UnsupportedSchema(_) => ErrorKind::UnsupportedSchema,
            Self::InvalidSuite(_) | Self::Parse(_) => ErrorKind::InvalidSuite,
        }
    }

    pub(crate) fn invalid(message: impl Into<String>) -> Self {
        Self::InvalidSuite(message.into())
    }
}
