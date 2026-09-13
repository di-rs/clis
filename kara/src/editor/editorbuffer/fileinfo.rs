use std::{fmt::Display, path::PathBuf};

#[derive(Default, Debug, Clone)]
pub struct FileInfo {
    pub(crate) path: PathBuf,
}

impl FileInfo {
    pub fn from(file_name: &str) -> Self {
        Self {
            path: PathBuf::from(file_name),
        }
    }
}

impl Display for FileInfo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let filename = self
            .path
            .file_name()
            .and_then(|x| x.to_str())
            .unwrap_or("[No Name]");
        write!(f, "{filename}")
    }
}
