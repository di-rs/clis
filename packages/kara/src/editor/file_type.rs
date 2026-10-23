use std::{fmt, path::Path};

#[derive(Default, Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileType {
    #[default]
    PlainText,
    Rust,
}

impl FileType {
    pub fn detect(path: Option<&Path>) -> Self {
        if path
            .and_then(Path::extension)
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("rs"))
        {
            Self::Rust
        } else {
            Self::PlainText
        }
    }
}

impl fmt::Display for FileType {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::PlainText => "Text",
            Self::Rust => "Rust",
        })
    }
}
