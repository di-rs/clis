//! The versioned, strict data model shared by tests and reference recording.

use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Component, Path, PathBuf},
};

/// A package-owned catalog. Paths in fixture fields are relative to `tests/`.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Suite {
    pub schema: u32,
    pub binary: String,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub references: BTreeMap<String, Reference>,
    pub cases: Vec<Case>,
    #[serde(skip)]
    pub(crate) package_dir: PathBuf,
    #[serde(skip)]
    pub(crate) source: String,
}

/// An explicitly selected independent executable; no PATH fallback is performed.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Reference {
    pub program: PathBuf,
    pub platform: String,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub version_args: Vec<String>,
}

/// A single named invocation and its observable contract.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Case {
    pub name: String,
    #[serde(default)]
    pub execution: Execution,
    pub args: Vec<String>,
    /// Extra input files or directories used by file operands, relative to tests/.
    #[serde(default)]
    pub inputs: Vec<PathBuf>,
    pub stdin: Option<Input>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    pub origin: Origin,
    pub expect: Expected,
    pub adapt: Option<Adaptation>,
}

/// Who executes a candidate case. Rust recipes still support reference capture.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum Execution {
    #[default]
    Toml,
    Rust,
}

/// Stdin transport is explicit, even for an empty pipe.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "mode", rename_all = "kebab-case", deny_unknown_fields)]
pub enum Input {
    Pipe {
        text: Option<String>,
        #[serde(default)]
        files: Vec<PathBuf>,
    },
    File {
        path: PathBuf,
    },
}

/// Independent reference recipe or a documented manually derived expectation.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Origin {
    pub reference: Option<String>,
    pub manual: Option<String>,
    pub args: Option<Vec<String>>,
    pub exit: Option<i32>,
    pub stderr: Option<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    pub reason: Option<String>,
}

/// Both streams and an exact numeric exit code are required.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Expected {
    pub exit: i32,
    pub stdout: Option<String>,
    pub stdout_file: Option<PathBuf>,
    pub stderr: Option<String>,
    pub stderr_file: Option<PathBuf>,
}

/// A narrow, explained transformation of reference bytes, never candidate output.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Adaptation {
    pub expected_stdout: Transform,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Transform {
    Utf8Lossy,
}

impl Transform {
    pub(crate) fn apply(self, bytes: &[u8]) -> Vec<u8> {
        match self {
            Self::Utf8Lossy => String::from_utf8_lossy(bytes).into_owned().into_bytes(),
        }
    }
}

impl Suite {
    /// Load and validate a catalog, including its fixture references.
    ///
    /// # Errors
    /// Returns contextual errors for malformed data or missing fixtures.
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().canonicalize()?;
        let tests = path
            .parent()
            .ok_or_else(|| Error::new("catalog has no parent"))?;
        if path.file_name().and_then(|name| name.to_str()) != Some("cases.toml")
            || tests.file_name().and_then(|name| name.to_str()) != Some("tests")
        {
            return Err(Error::new(
                "catalog must be the owning package's tests/cases.toml",
            ));
        }
        let text = fs::read_to_string(&path)?;
        let mut suite: Self = toml::from_str(&text)
            .map_err(|error| Error::caused_by(format!("cannot parse {}", path.display()), error))?;
        suite.package_dir = tests
            .parent()
            .ok_or_else(|| Error::new("tests has no package parent"))?
            .to_path_buf();
        suite.source = text;
        suite.validate()?;
        Ok(suite)
    }

    #[must_use]
    pub fn package_dir(&self) -> &Path {
        &self.package_dir
    }

    #[must_use]
    pub fn path(&self) -> PathBuf {
        self.package_dir.join("tests/cases.toml")
    }

    /// Resolve one case by its stable name.
    ///
    /// # Errors
    /// Returns an error naming the catalog and unknown case.
    pub fn case(&self, name: &str) -> Result<&Case> {
        self.cases
            .iter()
            .find(|case| case.name == name)
            .ok_or_else(|| {
                Error::new(format!(
                    "unknown case {name:?} in {}",
                    self.path().display()
                ))
            })
    }

    /// Resolve a validated fixture path within the package's tests directory.
    ///
    /// # Errors
    /// Rejects absolute paths, parent components, and paths outside inputs/expected.
    pub fn fixture_path(&self, path: &Path) -> Result<PathBuf> {
        relative_path(path)?;
        if !path.starts_with("inputs") && !path.starts_with("expected") {
            return Err(Error::new(format!(
                "fixture must be under inputs/ or expected/: {}",
                path.display()
            )));
        }
        checked_path(&self.package_dir.join("tests"), path)
    }

    /// Read fixture bytes without any text conversion.
    ///
    /// # Errors
    /// Reports the failing fixture's full path.
    pub fn read(&self, path: &Path) -> Result<Vec<u8>> {
        let path = self.fixture_path(path)?;
        fs::read(&path)
            .map_err(|error| Error::caused_by(format!("cannot read {}", path.display()), error))
    }

    /// Find expected files with no catalog owner, including Rust-owned recipes.
    ///
    /// # Errors
    /// Returns filesystem errors when enumerating fixtures.
    pub fn unreferenced_expected(&self) -> Result<Vec<PathBuf>> {
        let owned: BTreeSet<_> = self
            .cases
            .iter()
            .flat_map(|case| case.expect.paths())
            .cloned()
            .collect();
        let root = self.package_dir.join("tests");
        Ok(files_under(&root.join("expected"))?
            .into_iter()
            .filter_map(|path| path.strip_prefix(&root).ok().map(Path::to_path_buf))
            .filter(|path| !owned.contains(path))
            .collect())
    }

    fn validate(&self) -> Result {
        if self.schema != 1 || !identifier(&self.binary) || self.cases.is_empty() {
            return Err(Error::new(
                "catalog requires schema = 1, a binary name, and nonempty cases",
            ));
        }
        let manifest: toml::Value =
            toml::from_str(&fs::read_to_string(self.package_dir.join("Cargo.toml"))?)
                .map_err(|error| Error::caused_by("cannot parse owning Cargo.toml", error))?;
        if manifest
            .get("package")
            .and_then(|p| p.get("name"))
            .and_then(toml::Value::as_str)
            != Some(self.binary.as_str())
        {
            return Err(Error::new(
                "catalog binary must match the owning Cargo package",
            ));
        }
        for (name, reference) in &self.references {
            if !identifier(name)
                || !reference.program.is_absolute()
                || reference.platform.is_empty()
            {
                return Err(Error::new(format!(
                    "reference {name:?} requires an absolute program and platform"
                )));
            }
        }
        let mut names = BTreeSet::new();
        for case in &self.cases {
            if !identifier(&case.name) || !names.insert(&case.name) {
                return Err(Error::new(format!(
                    "invalid or duplicate case name {:?}",
                    case.name
                )));
            }
            case.validate(self).map_err(|error| {
                Error::caused_by(format!("invalid case {:?}", case.name), error)
            })?;
        }
        Ok(())
    }
}

impl Case {
    fn validate(&self, suite: &Suite) -> Result {
        self.expect.bytes(suite)?;
        if !(0..=255).contains(&self.expect.exit)
            || self
                .origin
                .exit
                .is_some_and(|code| !(0..=255).contains(&code))
        {
            return Err(Error::new("exit codes must be between 0 and 255"));
        }
        match (&self.origin.reference, &self.origin.manual) {
            (Some(name), None) if suite.references.contains_key(name) => {}
            (None, Some(reason)) if !reason.trim().is_empty() => {}
            _ => {
                return Err(Error::new(
                    "origin requires one known reference or a nonempty manual explanation",
                ));
            }
        }
        if self.origin.reference.is_none()
            && (self.origin.args.is_some()
                || self.origin.exit.is_some()
                || self.origin.stderr.is_some()
                || !self.origin.env.is_empty()
                || self.adapt.is_some())
        {
            return Err(Error::new(
                "manual expectations cannot contain reference overrides or adaptations",
            ));
        }
        if (self
            .origin
            .exit
            .is_some_and(|exit| exit != self.expect.exit)
            || self.origin.args.is_some()
            || self.origin.stderr.is_some()
            || !self.origin.env.is_empty())
            && self
                .origin
                .reason
                .as_ref()
                .is_none_or(|reason| reason.trim().is_empty())
        {
            return Err(Error::new("reference overrides require origin.reason"));
        }
        if self
            .adapt
            .as_ref()
            .is_some_and(|adapt| adapt.reason.trim().is_empty())
        {
            return Err(Error::new("adaptations require a nonempty reason"));
        }
        for path in self
            .inputs
            .iter()
            .chain(self.stdin.iter().flat_map(Input::paths))
        {
            if !path.starts_with("inputs") || !suite.fixture_path(path)?.exists() {
                return Err(Error::new(format!(
                    "missing input fixture {}",
                    path.display()
                )));
            }
        }
        if let Some(stdin) = &self.stdin {
            stdin.bytes(suite)?;
        }
        Ok(())
    }
}

impl Expected {
    pub(crate) fn paths(&self) -> impl Iterator<Item = &PathBuf> {
        self.stdout_file.iter().chain(&self.stderr_file)
    }

    pub(crate) fn bytes(&self, suite: &Suite) -> Result<(Vec<u8>, Vec<u8>)> {
        Ok((
            stream(
                suite,
                "stdout",
                self.stdout.as_deref(),
                self.stdout_file.as_deref(),
            )?,
            stream(
                suite,
                "stderr",
                self.stderr.as_deref(),
                self.stderr_file.as_deref(),
            )?,
        ))
    }
}

impl Input {
    pub(crate) fn paths(&self) -> Vec<&PathBuf> {
        match self {
            Self::Pipe { files, .. } => files.iter().collect(),
            Self::File { path } => vec![path],
        }
    }

    pub(crate) fn bytes(&self, suite: &Suite) -> Result<Vec<u8>> {
        match self {
            Self::Pipe {
                text: Some(text),
                files,
            } if files.is_empty() => Ok(text.as_bytes().to_vec()),
            Self::Pipe { text: None, files } if !files.is_empty() => {
                let mut bytes = Vec::new();
                for path in files {
                    bytes.extend(suite.read(path)?);
                }
                Ok(bytes)
            }
            Self::File { path } => suite.read(path),
            Self::Pipe { .. } => Err(Error::new(
                "pipe stdin requires either text (possibly empty) or nonempty files",
            )),
        }
    }
}

fn stream(suite: &Suite, name: &str, text: Option<&str>, path: Option<&Path>) -> Result<Vec<u8>> {
    match (text, path) {
        (Some(text), None) => Ok(text.as_bytes().to_vec()),
        (None, Some(path)) if path.starts_with("expected") => suite.read(path),
        _ => Err(Error::new(format!(
            "expect requires exactly one of {name} or {name}_file under expected/"
        ))),
    }
}

pub(crate) fn relative_path(path: &Path) -> Result {
    if path.as_os_str().is_empty()
        || !path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
    {
        return Err(Error::new(format!(
            "path must be relative without parent components: {}",
            path.display()
        )));
    }
    Ok(())
}

pub(crate) fn checked_path(root: &Path, relative: &Path) -> Result<PathBuf> {
    relative_path(relative)?;
    let mut path = root.to_path_buf();
    for part in relative.components() {
        path.push(part);
        let metadata = fs::symlink_metadata(&path).map_err(|error| {
            Error::caused_by(format!("cannot inspect {}", path.display()), error)
        })?;
        if metadata.file_type().is_symlink() {
            return Err(Error::new(format!(
                "symlink fixtures are unsupported: {}",
                path.display()
            )));
        }
    }
    Ok(path)
}

fn identifier(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

pub(crate) fn files_under(root: &Path) -> Result<Vec<PathBuf>> {
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut files = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            files.extend(files_under(&entry.path())?);
        } else {
            files.push(entry.path());
        }
    }
    files.sort();
    Ok(files)
}
