use clap::{ValueEnum, builder::PossibleValue};
use regex::Regex;
use walkdir::DirEntry;

#[derive(Debug, PartialEq, Eq, Clone)]
pub enum EntryType {
    Dir,
    File,
    Link,
}

impl ValueEnum for EntryType {
    fn value_variants<'a>() -> &'a [Self] {
        &[Self::Dir, Self::File, Self::Link]
    }

    fn to_possible_value(&self) -> Option<clap::builder::PossibleValue> {
        Some(match self {
            Self::Dir => PossibleValue::new("d"),
            Self::File => PossibleValue::new("f"),
            Self::Link => PossibleValue::new("l"),
        })
    }
}

#[must_use]
pub fn is_type_matches(entry: &DirEntry, entry_types: &[EntryType]) -> bool {
    if entry_types.is_empty() {
        return true;
    }

    entry_types.iter().any(|t| match t {
        EntryType::Dir => entry.file_type().is_dir(),
        EntryType::File => entry.file_type().is_file(),
        EntryType::Link => entry.file_type().is_symlink(),
    })
}

#[must_use]
pub fn is_name_matches(entry: &DirEntry, names: &[Regex]) -> bool {
    if names.is_empty() {
        return true;
    }

    let file_name = entry.file_name().to_string_lossy();

    names.iter().any(|r| r.is_match(&file_name))
}
