#[cfg(not(test))]
use log::warn;
use regex::Regex;
use std::{
    io::{BufRead, Write},
    path::{Path, PathBuf},
};
use walkdir::WalkDir;

#[derive(Debug, thiserror::Error)]
pub enum ParseError {
    #[error("{0} is a directory")]
    DirectryWithNonRecursive(String),
    #[error("{0} doesn't exists")]
    PathNotExists(String),
    #[error(transparent)]
    WalkDirError(#[from] walkdir::Error),
}

#[must_use]
pub fn find_files(paths: &[PathBuf], recursive: bool) -> Vec<Result<PathBuf, ParseError>> {
    let mut res = Vec::new();

    for path in paths {
        if path == Path::new("-") {
            res.push(Ok(path.clone()));
            continue;
        }

        let is_dir = path.is_dir();
        let exists = path.exists();

        if !exists {
            res.push(Err(ParseError::PathNotExists(path.display().to_string())));
        } else if is_dir && !recursive {
            res.push(Err(ParseError::DirectryWithNonRecursive(
                path.display().to_string(),
            )));
        } else if is_dir {
            let mut dir_files = walk_dir_tree(path);
            res.append(&mut dir_files);
        } else {
            res.push(Ok(path.clone()));
        }
    }
    res
}

#[allow(clippy::redundant_closure_for_method_calls)]
fn walk_dir_tree(dir: &PathBuf) -> Vec<Result<PathBuf, ParseError>> {
    WalkDir::new(dir)
        .into_iter()
        .skip(1)
        .map(|entry| {
            entry
                .map(|e| e.into_path())
                .map_err(ParseError::WalkDirError)
        })
        .collect()
}

/// # Errors
/// Throws error when couldn't read the line or writing to the writer fails
pub fn find_matches(
    reader: impl BufRead,
    mut writer: impl Write,
    pattern: &Regex,
    invert: bool,
) -> Result<(), std::io::Error> {
    let write_line = |line: &str| -> Result<(), std::io::Error> { write!(writer, "{line}") };

    run_matches_finder(reader, pattern, invert, write_line)?;

    Ok(())
}

/// # Errors
/// Throws error when couldn't read the line or writing to the writer fails
pub fn count_matches(
    reader: impl BufRead,
    mut writer: impl Write,
    pattern: &Regex,
    invert: bool,
) -> Result<(), std::io::Error> {
    let mut count: usize = 0;

    let increase_count = |_: &str| {
        count = count.saturating_add(1);
        Ok(())
    };

    run_matches_finder(reader, pattern, invert, increase_count)?;

    writeln!(writer, "{count}")?;
    Ok(())
}

fn run_matches_finder<F>(
    mut reader: impl BufRead,
    pattern: &Regex,
    invert: bool,
    mut cb: F,
) -> Result<(), std::io::Error>
where
    F: FnMut(&str) -> Result<(), std::io::Error>,
{
    let mut line = String::new();

    while let bytes = reader.read_line(&mut line)?
        && bytes != 0
    {
        let matches_pattern = pattern.is_match(&line);
        if matches_pattern ^ invert {
            cb(&line)?;
            #[cfg(not(test))]
            warn!("found line with pattern: {pattern} - {line}");
        }

        line.clear();
    }

    Ok(())
}

#[cfg(test)]
#[allow(
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::panic_in_result_fn
)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;
    use regex::RegexBuilder;
    use std::{error::Error, io::Cursor};

    #[test]
    fn find_files_one_file() {
        let files = find_files(&["./tests/inputs/fox.txt".into()], false);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].as_ref().unwrap(), "./tests/inputs/fox.txt");
    }

    #[test]
    fn find_files_non_recursive_directory() {
        let files = find_files(&["./tests/inputs".into()], false);
        assert_eq!(files.len(), 1);
        assert_eq!(
            files[0].as_ref().unwrap_err().to_string(),
            "./tests/inputs is a directory"
        );
    }

    #[test]
    fn find_files_in_directory() {
        let files = find_files(&["./tests/inputs".into()], true);
        let mut files: Vec<_> = files.iter().map(|f| f.as_ref().unwrap()).collect();
        files.sort();

        assert_eq!(files.len(), 4);
        assert_eq!(
            files,
            vec![
                "./tests/inputs/bustle.txt",
                "./tests/inputs/empty.txt",
                "./tests/inputs/fox.txt",
                "./tests/inputs/nobody.txt",
            ]
        );
    }

    #[test]
    fn find_files_bad_file() {
        let files = find_files(&["./tests/file/doesnt/exists".into()], true);

        assert_eq!(files.len(), 1);
        assert!(files[0].is_err());
    }

    #[test]
    fn find_a_match() -> Result<(), Box<dyn Error>> {
        let mut result = Vec::new();
        let text = b"lorem ipsum\ndelor sit amet";
        let pattern = Regex::new("lo*rem")?;

        find_matches(Cursor::new(&text), &mut result, &pattern, false)?;

        assert_eq!(result, b"lorem ipsum\n");
        Ok(())
    }

    #[test]
    fn find_another_match() -> Result<(), Box<dyn Error>> {
        let mut result = Vec::new();
        let text = b"Lorem\nIpsum\r\nDOLOR";
        #[allow(clippy::trivial_regex)]
        let pattern = Regex::new("or")?;

        let matches = find_matches(Cursor::new(&text), &mut result, &pattern, false);

        assert!(matches.is_ok());
        assert_eq!(result, b"Lorem\n");
        Ok(())
    }

    #[test]
    fn find_another_match_inverted() -> Result<(), Box<dyn Error>> {
        let mut result = Vec::new();
        let text = b"Lorem\nIpsum\r\nDOLOR";
        #[allow(clippy::trivial_regex)]
        let pattern = Regex::new("or")?;

        let matches = find_matches(Cursor::new(&text), &mut result, &pattern, true);

        assert!(matches.is_ok());
        assert_eq!(result, b"Ipsum\r\nDOLOR");
        Ok(())
    }

    #[test]
    fn find_math_case_insensitive() -> Result<(), Box<dyn Error>> {
        let mut result = Vec::new();
        let text = b"Lorem\nIpsum\r\nDOLOR";
        #[allow(clippy::trivial_regex)]
        let pattern = RegexBuilder::new("or").case_insensitive(true).build()?;

        let matches = find_matches(Cursor::new(&text), &mut result, &pattern, false);

        assert!(matches.is_ok());
        assert_eq!(result, b"Lorem\nDOLOR");
        Ok(())
    }
}
