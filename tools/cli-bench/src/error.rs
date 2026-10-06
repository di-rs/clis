/// Stable classification for callers deciding how to report a suite failure.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ErrorKind {
    UnsupportedSchema,
    InvalidSuite,
    Evidence,
    Execution,
    Io,
}

/// Configuration, evidence or execution failure; CLI exit mapping stays at the edge.
#[derive(Debug, thiserror::Error)]
pub enum BenchError {
    #[error("unsupported suite schema version {0}; expected 1")]
    UnsupportedSchema(u32),
    #[error("invalid suite: {0}")]
    InvalidSuite(String),
    #[error("invalid suite TOML: {0}")]
    Parse(#[from] toml::de::Error),
    #[error("evidence error: {0}")]
    Evidence(String),
    #[error("execution error: {0}")]
    Execution(String),
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("evidence JSON: {0}")]
    Json(#[from] serde_json::Error),
}

impl BenchError {
    /// Classify a failure without inspecting diagnostic wording.
    #[must_use]
    pub const fn kind(&self) -> ErrorKind {
        match self {
            Self::Evidence(_) | Self::Json(_) => ErrorKind::Evidence,
            Self::Io(_) => ErrorKind::Io,
            Self::Execution(_) => ErrorKind::Execution,
            Self::UnsupportedSchema(_) => ErrorKind::UnsupportedSchema,
            Self::InvalidSuite(_) | Self::Parse(_) => ErrorKind::InvalidSuite,
        }
    }

    pub(crate) fn invalid(message: impl Into<String>) -> Self {
        Self::InvalidSuite(message.into())
    }
}
