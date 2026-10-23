use super::{Page, Report};
use anyhow::{Context as _, Result};
use cli_testing::catalog::Suite;
use serde_json::json;
use std::{fmt::Write as _, fs, path::Path};

pub(super) fn discover(root: &Path, page: &Page) -> Result<Report> {
    let root = root
        .canonicalize()
        .context("discovery root is unavailable")?;
    let mut directories = vec![root.clone()];
    let mut catalogs = Vec::new();
    while let Some(directory) = directories.pop() {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let kind = entry.file_type()?;
            let path = entry.path();
            if kind.is_dir() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if !name.starts_with('.') && !matches!(name.as_ref(), "target" | "node_modules") {
                    directories.push(path);
                }
            } else if kind.is_file()
                && entry.file_name() == "cases.toml"
                && path
                    .parent()
                    .and_then(Path::file_name)
                    .is_some_and(|name| name == "tests")
            {
                catalogs.push(path);
            }
        }
    }
    catalogs.sort();
    let (selected, next_offset) = page.select(&catalogs);
    let mut items = Vec::new();
    let mut text = String::new();
    for path in selected {
        let suite = Suite::load(path)?;
        items.push(
            json!({"suite": suite.path(), "binary": suite.binary, "cases": suite.cases.len()}),
        );
        let _ = writeln!(
            text,
            "{}: {} ({} cases)",
            suite.binary,
            path.display(),
            suite.cases.len()
        );
    }
    Ok(Report::new(
        json!({"root": root, "suites": items, "total": catalogs.len(), "next_offset": next_offset}),
        text,
    ))
}
