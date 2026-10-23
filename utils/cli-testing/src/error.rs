use std::{error, fmt, io};

/// Failure while preparing, executing, or checking a CLI case.
#[derive(Debug)]
pub struct Error {
    message: String,
    source: Option<Box<dyn error::Error + Send + Sync>>,
}

impl Error {
    /// Describe a failed test operation.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            source: None,
        }
    }

    /// Attach the underlying error without repeating it in the message.
    pub fn caused_by(
        message: impl Into<String>,
        source: impl error::Error + Send + Sync + 'static,
    ) -> Self {
        Self {
            message: message.into(),
            source: Some(Box::new(source)),
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl error::Error for Error {
    fn source(&self) -> Option<&(dyn error::Error + 'static)> {
        self.source
            .as_deref()
            .map(|source| -> &(dyn error::Error + 'static) { source })
    }
}

impl From<io::Error> for Error {
    fn from(source: io::Error) -> Self {
        Self::caused_by("fixture or process I/O failed", source)
    }
}

/// Result used by case runners and attribute-based tests.
pub type Result<T = ()> = std::result::Result<T, Error>;
