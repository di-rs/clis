use crate::{BenchError, CaseSpec, DatasetSet};
use std::path::{Path, PathBuf};

/// Validated suite case identifier, independent of filesystem paths.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CaseId(String);

impl CaseId {
    /// Validate one nonempty portable identifier.
    /// # Errors
    /// Rejects identifiers outside the suite identifier grammar.
    pub fn new(value: impl Into<String>) -> Result<Self, BenchError> {
        let value = value.into();
        crate::suite::validate_identifier(&value)?;
        Ok(Self(value))
    }
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Exclusive reset capability for a newly allocated, marked case directory.
#[derive(Debug)]
pub struct OwnedScratch {
    root: PathBuf,
    owner: PathBuf,
    path: PathBuf,
    case: CaseId,
    initial_mode: std::fs::Permissions,
}
impl OwnedScratch {
    /// Workload root, excluding the ownership marker.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }
}

/// Allocate a unique case root beneath an existing caller-supplied cache directory.
/// # Errors
/// Rejects symlink roots and filesystem failures; marks only a fresh owned child.
pub fn create_scratch(root: &Path, case: &CaseId) -> Result<OwnedScratch, BenchError> {
    real_directory(root)?;
    let parent = root.join("scratch");
    match std::fs::create_dir(&parent) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    real_directory(&parent)?;
    let owner = crate::store::unique_directory(&parent, case.as_str())?;
    std::fs::write(owner.join(".cli-bench-scratch"), case.as_str())?;
    let path = owner.join("work");
    std::fs::create_dir(&path)?;
    let initial_mode = std::fs::metadata(&path)?.permissions();
    let scratch = OwnedScratch {
        root: root.into(),
        owner,
        path,
        case: case.clone(),
        initial_mode,
    };
    scratch.verify()?;
    Ok(scratch)
}
pub fn real_directory(path: &Path) -> Result<(), BenchError> {
    crate::process::utf8_path(path)?;
    let mut current = PathBuf::new();
    for component in path.components() {
        if !matches!(
            component,
            std::path::Component::RootDir | std::path::Component::Normal(_)
        ) {
            return Err(BenchError::invalid(
                "scratch paths require absolute normal components",
            ));
        }
        current.push(component);
        if !std::fs::symlink_metadata(&current)?.is_dir() {
            return Err(BenchError::invalid(
                "scratch directory contains a symlink or non-directory",
            ));
        }
    }
    Ok(())
}

/// Restore and verify the declared initial directory state outside timing.
/// # Errors
/// Rejects changed inputs, ownership, symlinks or a mismatched case before deletion.
pub fn reset_case(
    case: &CaseSpec,
    datasets: &DatasetSet,
    scratch: &OwnedScratch,
) -> Result<(), BenchError> {
    crate::verify_datasets(datasets)?;
    crate::suite::validate_case(case, &datasets.inputs.keys().map(String::as_str).collect())?;
    scratch.verify()?;
    if case.id != scratch.case.as_str() {
        return Err(BenchError::invalid("scratch belongs to a different case"));
    }
    // Inspect the entire tree before touching any entry. Cooperative callers must
    // keep these paths stable; this does not claim race-proof hostile isolation.
    entries(scratch.path())?;
    std::fs::set_permissions(scratch.path(), scratch.initial_mode.clone())?;
    for entry in std::fs::read_dir(scratch.path())? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            std::fs::remove_dir_all(entry.path())?;
        } else {
            std::fs::remove_file(entry.path())?;
        }
    }
    let mut expected = std::collections::BTreeSet::new();
    if let crate::MutationSetup::Directories { paths } = &case.mutation {
        for relative in paths {
            std::fs::create_dir_all(scratch.path().join(relative))?;
            let mut path = Path::new(relative);
            while !path.as_os_str().is_empty() {
                expected.insert(
                    path.to_str()
                        .ok_or_else(|| BenchError::invalid("non-UTF-8 reset path"))?
                        .to_owned(),
                );
                path = path.parent().unwrap_or_else(|| Path::new(""));
            }
        }
    }
    let actual = entries(scratch.path())?;
    if actual
        .keys()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>()
        != expected
        || actual.values().any(|entry| !entry.directory)
    {
        return Err(BenchError::Evidence(
            "reset state differs from declared directories".into(),
        ));
    }
    Ok(())
}
impl OwnedScratch {
    pub(crate) fn verify(&self) -> Result<(), BenchError> {
        real_directory(&self.root)?;
        real_directory(&self.owner)?;
        real_directory(&self.path)?;
        if !self.owner.starts_with(self.root.join("scratch"))
            || self.path != self.owner.join("work")
        {
            return Err(BenchError::Evidence("scratch containment changed".into()));
        }
        let marker = self.owner.join(".cli-bench-scratch");
        if !std::fs::symlink_metadata(&marker)?.is_file()
            || std::fs::read(marker)? != self.case.as_str().as_bytes()
        {
            return Err(BenchError::Evidence(
                "scratch ownership marker changed".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Entry {
    pub directory: bool,
    pub mode: u32,
}
pub fn entries(root: &Path) -> Result<std::collections::BTreeMap<String, Entry>, BenchError> {
    use std::os::unix::fs::PermissionsExt;
    fn walk(
        root: &Path,
        path: &Path,
        result: &mut std::collections::BTreeMap<String, Entry>,
    ) -> Result<(), BenchError> {
        for entry in std::fs::read_dir(path)? {
            let entry = entry?;
            let path = entry.path();
            let metadata = std::fs::symlink_metadata(&path)?;
            if !metadata.is_dir() && !metadata.is_file() {
                return Err(BenchError::invalid(
                    "scratch contains a symlink or special file",
                ));
            }
            let relative = path
                .strip_prefix(root)
                .map_err(|error| BenchError::Evidence(error.to_string()))?;
            let key = relative
                .to_str()
                .ok_or_else(|| BenchError::invalid("scratch contains non-UTF-8 path"))?
                .to_owned();
            result.insert(
                key,
                Entry {
                    directory: metadata.is_dir(),
                    mode: metadata.permissions().mode() & 0o7777,
                },
            );
            if metadata.is_dir() {
                walk(root, &path, result)?;
            }
        }
        Ok(())
    }
    real_directory(root)?;
    let mut result = std::collections::BTreeMap::new();
    walk(root, root, &mut result)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{MutationSetup, Store};
    use std::{fs, os::unix::fs::symlink};
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    fn require(value: bool, message: &str) -> TestResult {
        if value { Ok(()) } else { Err(message.into()) }
    }
    fn case() -> Result<CaseSpec, BenchError> {
        let mut case = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?
            .cases
            .into_iter()
            .next()
            .ok_or_else(|| BenchError::invalid("fixture case"))?;
        case.argv.clear();
        case.correctness = vec![crate::CorrectnessRule::EmptyStderr {}];
        Ok(case)
    }
    #[test]
    fn reset_rejects_symlink_escape() -> TestResult {
        let temp = assert_fs::TempDir::new()?;
        let store = Store::open(&temp.path().join("store"))?;
        let case = case()?;
        let scratch = create_scratch(store.root(), &CaseId::new(case.id.clone())?)?;
        let outside = temp.path().join("outside");
        fs::create_dir(&outside)?;
        fs::write(outside.join("keep"), "safe")?;
        symlink(&outside, scratch.path().join("escape"))?;
        require(
            reset_case(&case, &DatasetSet::default(), &scratch).is_err(),
            "unsafe reset accepted",
        )?;
        require(
            fs::read(outside.join("keep"))? == b"safe",
            "outside file changed",
        )?;
        Ok(())
    }
    #[test]
    fn reset_restores_absent_and_existing_directory_states() -> TestResult {
        let temp = assert_fs::TempDir::new()?;
        let store = Store::open(&temp.path().join("store"))?;
        let mut case = case()?;
        let scratch = create_scratch(store.root(), &CaseId::new(case.id.clone())?)?;
        for paths in [vec![], vec!["parent/child".to_owned()]] {
            case.mutation = MutationSetup::Directories {
                paths: paths.clone(),
            };
            for _ in 0..2 {
                reset_case(&case, &DatasetSet::default(), &scratch)?;
                require(
                    scratch.path().join("parent/child").is_dir() != paths.is_empty(),
                    "wrong initial directories",
                )?;
                require(!scratch.path().join("changed").exists(), "leftover state")?;
                fs::write(scratch.path().join("changed"), "leftover")?;
            }
        }
        Ok(())
    }
    #[test]
    fn allocation_rejects_symlink_roots_without_overwriting_existing_children() -> TestResult {
        let temp = assert_fs::TempDir::new()?;
        let id = CaseId::new("case")?;
        let canonical = fs::canonicalize(temp.path())?;
        let first = create_scratch(&canonical, &id)?;
        let second = create_scratch(&canonical, &id)?;
        require(first.path() != second.path(), "scratch was reused")?;
        let store = Store::open(&temp.path().join("store"))?;
        let link = temp.path().join("alias");
        symlink(store.root(), &link)?;
        require(create_scratch(&link, &id).is_err(), "symlink root accepted")?;
        Ok(())
    }
    #[test]
    fn reset_rejects_replaced_owner_and_wrong_case() -> TestResult {
        let temp = assert_fs::TempDir::new()?;
        let store = Store::open(&temp.path().join("store"))?;
        let mut case = case()?;
        let scratch = create_scratch(store.root(), &CaseId::new(case.id.clone())?)?;
        case.id = "other".into();
        require(
            reset_case(&case, &DatasetSet::default(), &scratch).is_err(),
            "unsafe reset accepted",
        )?;
        case.id = scratch.case.as_str().into();
        fs::remove_dir(scratch.path())?;
        symlink(temp.path(), scratch.path())?;
        require(
            reset_case(&case, &DatasetSet::default(), &scratch).is_err(),
            "unsafe reset accepted",
        )?;
        Ok(())
    }
    #[test]
    fn reset_restores_work_root_mode_and_rejects_marker_tampering() -> TestResult {
        use std::os::unix::fs::PermissionsExt;
        let temp = assert_fs::TempDir::new()?;
        let root = fs::canonicalize(temp.path())?;
        let case = case()?;
        let scratch = create_scratch(&root, &CaseId::new(case.id.clone())?)?;
        let original = fs::metadata(scratch.path())?.permissions().mode();
        fs::set_permissions(scratch.path(), fs::Permissions::from_mode(0o701))?;
        reset_case(&case, &DatasetSet::default(), &scratch)?;
        require(
            fs::metadata(scratch.path())?.permissions().mode() == original,
            "reset kept changed work root mode",
        )?;
        fs::write(scratch.owner.join(".cli-bench-scratch"), b"forged")?;
        require(
            reset_case(&case, &DatasetSet::default(), &scratch).is_err(),
            "tampered marker accepted",
        )?;
        Ok(())
    }
}
