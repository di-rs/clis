//! Package-owned suite discovery for the CLI; explicit file selection bypasses this.
use super::{AdapterScratch, build_environment};
use cli_bench::{ExecutionPolicy, ProcessRunner, parse_suite};
use std::path::PathBuf;

fn package_suite_source(
    metadata: &serde_json::Value,
    selected: &str,
) -> anyhow::Result<(PathBuf, String)> {
    let packages = metadata["packages"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("Cargo metadata omitted packages"))?;
    let members = metadata["workspace_members"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("Cargo metadata omitted workspace members"))?;
    let mut matches = packages.iter().filter(|package| {
        package["name"].as_str() == Some(selected)
            && package["id"]
                .as_str()
                .is_some_and(|id| members.iter().any(|member| member.as_str() == Some(id)))
    });
    let package = matches
        .next()
        .ok_or_else(|| anyhow::anyhow!("workspace package {selected:?} was not found"))?;
    anyhow::ensure!(
        matches.next().is_none(),
        "workspace package {selected:?} is ambiguous"
    );
    let declared = package
        .pointer("/metadata/cli-bench/suite")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| {
            anyhow::anyhow!("package {selected:?} must declare [package.metadata.cli-bench] suite")
        })?;
    let drive = declared.as_bytes().get(1) == Some(&b':');
    anyhow::ensure!(
        !declared.is_empty()
            && !declared.contains(['\\', '\0'])
            && !drive
            && declared
                .split('/')
                .all(|part| !matches!(part, "" | "." | "..")),
        "package {selected:?} suite must be a contained relative path"
    );
    let manifest = package["manifest_path"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("Cargo metadata omitted package manifest path"))?;
    let manifest = std::fs::canonicalize(manifest)?;
    let root = manifest
        .parent()
        .ok_or_else(|| anyhow::anyhow!("package manifest has no parent"))?;
    let path = std::fs::canonicalize(root.join(declared)).map_err(|error| {
        anyhow::anyhow!("cannot load package {selected:?} suite {declared:?}: {error}")
    })?;
    anyhow::ensure!(
        path.starts_with(root) && path.is_file(),
        "package {selected:?} suite escapes its package or is not a file"
    );
    let source = std::fs::read_to_string(path)?;
    anyhow::ensure!(
        parse_suite(&source)?.package == selected,
        "suite.package must match selected package {selected:?}"
    );
    let workspace = metadata["workspace_root"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("Cargo metadata omitted workspace root"))?;
    Ok((std::fs::canonicalize(workspace)?, source))
}
pub(super) fn discover_package_suite(
    cwd: &std::path::Path,
    manifest: Option<&std::path::Path>,
    selected: &str,
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<(PathBuf, String)> {
    let environment = build_environment();
    let runner = ProcessRunner::new(ExecutionPolicy {
        timeout: std::time::Duration::from_mins(30),
        max_stream_bytes: 268_435_456,
        cancellation: std::sync::Arc::clone(cancellation),
    });
    let search = environment
        .get("PATH")
        .ok_or_else(|| anyhow::anyhow!("PATH unavailable for Cargo package discovery"))?;
    // Keep the cargo filename: canonicalizing a rustup shim would invoke `rustup`.
    let cargo = std::env::split_paths(search)
        .map(|directory| cwd.join(directory).join("cargo"))
        .find(|path| path.is_file())
        .ok_or_else(|| {
            anyhow::anyhow!(
                "Cargo unavailable for package selection; supply an explicit --suite file instead"
            )
        })?;
    let mut arguments = vec![
        "metadata".into(),
        "--no-deps".into(),
        "--offline".into(),
        "--locked".into(),
        "--format-version".into(),
        "1".into(),
    ];
    if let Some(path) = manifest {
        arguments.extend([
            "--manifest-path".into(),
            path.to_str()
                .ok_or_else(|| anyhow::anyhow!("manifest path must be UTF-8"))?
                .into(),
        ]);
    }
    let capture = AdapterScratch::new()?;
    let paths = cli_bench::CapturePaths {
        stdout: capture.0.join("metadata.json"),
        stderr: capture.0.join("metadata.stderr"),
    };
    let outcome = runner.execute(
        &cli_bench::CommandSpec {
            program: cargo,
            argv: arguments,
            cwd: cwd.into(),
            environment,
            stdin: cli_bench::CommandInput::Null,
            stdout: cli_bench::CommandOutput::Capture,
        },
        &paths,
    )?;
    if let Err(error) = outcome.check_expected(0) {
        anyhow::bail!(
            "Cargo metadata failed for package {selected:?}: {error}\n{}",
            std::fs::read_to_string(paths.stderr)?
        );
    }
    let output = std::fs::read_to_string(paths.stdout)?;
    package_suite_source(&serde_json::from_str(&output)?, selected)
}

#[cfg(test)]
mod tests {
    fn package_fixture() -> anyhow::Result<(assert_fs::TempDir, serde_json::Value)> {
        let root = assert_fs::TempDir::new()?;
        std::fs::create_dir(root.path().join("src"))?;
        std::fs::create_dir(root.path().join("benches"))?;
        std::fs::write(root.path().join("src/lib.rs"), "")?;
        std::fs::write(
            root.path().join("Cargo.toml"),
            "[package]\nname = 'tailr'\nversion = '0.1.0'\nedition = '2024'\n[workspace]\n[package.metadata.cli-bench]\nsuite = 'benches/cli-bench.toml'\n",
        )?;
        std::fs::write(
            root.path().join("Cargo.lock"),
            "version = 4\n[[package]]\nname = 'tailr'\nversion = '0.1.0'\n",
        )?;
        std::fs::write(
            root.path().join("benches/cli-bench.toml"),
            include_str!("../../tests/inputs/minimal-suite.toml"),
        )?;
        let metadata = serde_json::json!({
            "workspace_root": root.path(), "workspace_members": ["tailr-id"],
            "packages": [{"id": "tailr-id", "name": "tailr", "manifest_path": root.path().join("Cargo.toml"), "metadata": {"cli-bench": {"suite": "benches/cli-bench.toml"}}}]
        });
        Ok((root, metadata))
    }

    #[test]
    fn package_discovery_requires_one_workspace_member_and_contained_matching_suite()
    -> anyhow::Result<()> {
        use std::os::unix::fs::symlink;
        let (root, metadata) = package_fixture()?;
        let (workspace, source) = super::package_suite_source(&metadata, "tailr")?;
        anyhow::ensure!(workspace == std::fs::canonicalize(root.path())?);
        anyhow::ensure!(cli_bench::parse_suite(&source)?.package == "tailr");
        anyhow::ensure!(super::package_suite_source(&metadata, "missing").is_err());
        let mut nonmember = metadata.clone();
        nonmember["workspace_members"] = serde_json::json!([]);
        anyhow::ensure!(super::package_suite_source(&nonmember, "tailr").is_err());
        let mut ambiguous = metadata.clone();
        ambiguous["packages"] =
            serde_json::json!([metadata["packages"][0], metadata["packages"][0]]);
        anyhow::ensure!(super::package_suite_source(&ambiguous, "tailr").is_err());
        let mut undeclared = metadata.clone();
        undeclared["packages"][0]["metadata"] = serde_json::Value::Null;
        anyhow::ensure!(super::package_suite_source(&undeclared, "tailr").is_err());
        for path in [
            "",
            "../suite.toml",
            "/suite.toml",
            "benches//suite.toml",
            "benches/./suite.toml",
            "C:escape",
            "missing.toml",
            "benches",
        ] {
            let mut invalid = metadata.clone();
            invalid["packages"][0]["metadata"]["cli-bench"]["suite"] = path.into();
            anyhow::ensure!(super::package_suite_source(&invalid, "tailr").is_err());
        }
        let outside = assert_fs::TempDir::new()?;
        std::fs::write(outside.path().join("suite.toml"), &source)?;
        std::fs::remove_file(root.path().join("benches/cli-bench.toml"))?;
        symlink(
            outside.path().join("suite.toml"),
            root.path().join("benches/cli-bench.toml"),
        )?;
        anyhow::ensure!(super::package_suite_source(&metadata, "tailr").is_err());
        std::fs::remove_file(root.path().join("benches/cli-bench.toml"))?;
        std::fs::write(
            root.path().join("benches/cli-bench.toml"),
            source.replace("package = \"tailr\"", "package = \"different\""),
        )?;
        anyhow::ensure!(super::package_suite_source(&metadata, "tailr").is_err());
        Ok(())
    }

    #[test]
    fn package_discovery_uses_offline_metadata_from_package_cwd_or_manifest() -> anyhow::Result<()>
    {
        let (root, _) = package_fixture()?;
        let cancellation = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let manifest = root.path().join("Cargo.toml");
        let ordinary = super::discover_package_suite(root.path(), None, "tailr", &cancellation)?;
        let outside = assert_fs::TempDir::new()?;
        let explicit =
            super::discover_package_suite(outside.path(), Some(&manifest), "tailr", &cancellation)?;
        anyhow::ensure!(ordinary == explicit);
        anyhow::ensure!(!root.path().join("target").exists());
        anyhow::ensure!(!outside.path().join("target").exists());
        Ok(())
    }
}
