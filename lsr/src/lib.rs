use chrono::{DateTime, Local};
use std::{fmt::Display, fs, os::unix::fs::MetadataExt, path::PathBuf};
use tabular::{Row, Table};

mod owner;
use owner::Owner;
use users::get_user_by_uid;

/// # Errors
/// Throws error is unable to read path
pub fn find_files(paths: &[PathBuf], show_hidden: bool) -> Result<Vec<PathBuf>, std::io::Error> {
    let mut results = Vec::new();

    for path in paths {
        if !path.exists() {
            eprintln!("{}: {}", path.display(), std::io::Error::last_os_error());
            continue;
        }

        if path.is_dir() {
            for entry in fs::read_dir(path)? {
                let entry = entry?;
                let path = entry.path();
                let is_hidden = path
                    .file_name()
                    .is_some_and(|filename| filename.to_string_lossy().starts_with('.'));
                if !is_hidden || show_hidden {
                    results.push(path);
                }
            }
        } else {
            results.push(path.to_owned());
        }
    }
    Ok(results)
}

/// # Errors
/// Throws error is unable to read path
pub fn get_formatted_output(paths: &[PathBuf]) -> Result<impl Display, std::io::Error> {
    #[allow(clippy::literal_string_with_formatting_args)]
    let fmt = "{:<}{:<}  {:>}  {:<}  {:<}  {:>}  {:<}  {:<}";
    let mut table = Table::new(fmt);

    for path in paths {
        let metadata = path.metadata()?;

        let file_type = if path.is_dir() { "d" } else { "-" };
        let perms = format_mode(metadata.mode());
        let link_count = metadata.nlink();

        let uid = metadata.uid();
        let user = get_user_by_uid(uid).map_or_else(
            || uid.to_string(),
            |u| u.name().to_string_lossy().to_string(),
        );

        let gid = metadata.gid();
        let group = get_user_by_uid(gid).map_or_else(
            || gid.to_string(),
            |u| u.name().to_string_lossy().to_string(),
        );

        let size = metadata.len();
        let modified: DateTime<Local> = DateTime::from(metadata.modified()?);
        let date = modified.format("%b %d %y %H:%M");
        let full_path = path.display();

        table.add_row(
            Row::new()
                .with_cell(file_type)
                .with_cell(perms)
                .with_cell(link_count)
                .with_cell(user)
                .with_cell(group)
                .with_cell(size)
                .with_cell(date)
                .with_cell(full_path),
        );
    }

    Ok(table)
}

fn format_mode(mode: u32) -> String {
    format!(
        "{}{}{}",
        permission_triple(mode, Owner::User),
        permission_triple(mode, Owner::Group),
        permission_triple(mode, Owner::Other),
    )
}

fn permission_triple(mode: u32, owner: Owner) -> String {
    let [read, write, execute] = owner.masks();
    format!(
        "{}{}{}",
        if mode & read == 0 { "-" } else { "r" },
        if mode & write == 0 { "-" } else { "w" },
        if mode & execute == 0 { "-" } else { "x" },
    )
}
