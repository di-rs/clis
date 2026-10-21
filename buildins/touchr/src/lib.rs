use chrono::NaiveDateTime;
use color_eyre::{Result, eyre::Context};
use std::{fs::File, path::Path, time::SystemTime};

/// # Errors
/// Throws error if cannot open or create file
pub fn file_proceed(
    file: &Path,
    timestamp: Option<NaiveDateTime>,
    skip_create: bool,
) -> Result<()> {
    let stats = std::fs::metadata(file);

    let file = match stats {
        Err(_) => {
            if skip_create {
                return Ok(());
            }
            create_missing(file)
                .wrap_err_with(|| format!("failed to create file {}", file.display()))?
        }
        Ok(_) => {
            File::open(file).wrap_err_with(|| format!("failed to open file {}", file.display()))?
        }
    };

    set_modified(&file, timestamp)
}

fn set_modified(file: &File, timestamp: Option<NaiveDateTime>) -> Result<()> {
    let time = timestamp.map_or_else(SystemTime::now, |timestamp| {
        SystemTime::from(timestamp.and_utc())
    });

    file.set_modified(time)
        .wrap_err("Failed to update file time")
}

fn create_missing(path: &Path) -> std::io::Result<File> {
    File::options()
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)
}

#[cfg(test)]
mod tests {
    use super::create_missing;

    #[test]
    fn creation_preserves_a_target_that_appeared_after_metadata() {
        let directory = assert_fs::TempDir::new().expect("temporary directory");
        let path = directory.path().join("raced");
        assert!(std::fs::metadata(&path).is_err());
        std::fs::write(&path, b"keep contents").expect("competing creator");
        let _file = create_missing(&path).expect("open racing target");
        assert_eq!(std::fs::read(&path).expect("read target"), b"keep contents");
    }
}
