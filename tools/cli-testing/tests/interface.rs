#![allow(
    clippy::panic_in_result_fn,
    reason = "Assertions verify the CLI contract; Result propagates fixture setup errors."
)]

use assert_fs::TempDir;
use cli_testing::{Command, cargo_bin_cmd};
use serde_json::Value;
use std::{fs, path::Path};

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

fn json_output(command: &mut Command, code: i32) -> Result<Value> {
    let assertion = command.assert().code(code).stderr("");
    let value: Value = serde_json::from_slice(&assertion.get_output().stdout)?;
    assert_eq!(value.get("schema").and_then(Value::as_u64), Some(1));
    assert_eq!(value.get("ok").and_then(Value::as_bool), Some(code == 0));
    Ok(value)
}

fn catalog(root: &Path) -> Result {
    fs::create_dir_all(root.join("tests"))?;
    fs::write(root.join("Cargo.toml"), "[package]\nname = 'sample'\n")?;
    fs::write(
        root.join("tests/cases.toml"),
        r#"schema = 1
binary = "sample"
[[cases]]
name = "first"
args = []
origin = { manual = "Empty output is the specified contract." }
expect = { exit = 0, stdout = "", stderr = "" }
[[cases]]
name = "custom"
execution = "rust"
args = []
origin = { manual = "A Rust-owned probe." }
expect = { exit = 0, stdout = "", stderr = "" }
"#,
    )?;
    Ok(())
}

#[test]
fn doctor_is_offline_and_runs_outside_a_repository() -> Result {
    let outside = TempDir::new()?;
    let result = json_output(
        cargo_bin_cmd!()
            .current_dir(outside.path())
            .args(["--json", "doctor"]),
        0,
    )?;
    assert_eq!(result["data"]["auth"], "not_required");
    assert_eq!(result["data"]["mode"], "offline");
    assert_eq!(result["data"]["fixtures_found"], false);
    catalog(outside.path())?;
    let result = json_output(
        cargo_bin_cmd!().current_dir(outside.path()).args([
            "doctor",
            "--json",
            "--suite",
            "tests/cases.toml",
        ]),
        0,
    )?;
    assert_eq!(result["data"]["fixtures_found"], true);
    Ok(())
}

#[test]
fn discovery_paginates_and_ignores_build_directories_and_symlinks() -> Result {
    let root = TempDir::new()?;
    catalog(&root.path().join("alpha"))?;
    catalog(&root.path().join("beta"))?;
    catalog(&root.path().join("target/hidden"))?;
    catalog(&root.path().join(".git/hidden"))?;
    #[cfg(unix)]
    std::os::unix::fs::symlink(root.path(), root.path().join("loop"))?;
    let result = json_output(
        cargo_bin_cmd!()
            .args(["--json", "discover", "--root"])
            .arg(root.path())
            .args(["--limit", "1"]),
        0,
    )?;
    assert_eq!(result["data"]["total"], 2);
    assert_eq!(result["data"]["next_offset"], 1);
    assert!(
        result["data"]["suites"][0]["suite"]
            .as_str()
            .ok_or("missing suite path")?
            .contains("alpha/tests/cases.toml")
    );
    let next = json_output(
        cargo_bin_cmd!()
            .args(["--json", "discover", "--root"])
            .arg(root.path())
            .args(["--limit", "1", "--offset", "1"]),
        0,
    )?;
    assert!(next["data"]["next_offset"].is_null());
    assert!(
        next["data"]["suites"][0]["suite"]
            .as_str()
            .ok_or("missing suite path")?
            .contains("beta/tests/cases.toml")
    );
    Ok(())
}

#[test]
fn cases_have_stable_ids_and_full_catalog_export() -> Result {
    let root = TempDir::new()?;
    catalog(root.path())?;
    let suite = root.path().join("tests/cases.toml");
    let result = json_output(
        cargo_bin_cmd!()
            .arg("--suite")
            .arg(&suite)
            .args(["--json", "list", "--limit", "1"]),
        0,
    )?;
    assert_eq!(result["data"]["cases"][0]["name"], "first");
    assert_eq!(result["data"]["next_offset"], 1);
    let result = json_output(
        cargo_bin_cmd!()
            .arg("--suite")
            .arg(&suite)
            .args(["--json", "list", "--offset", "1"]),
        0,
    )?;
    assert_eq!(result["data"]["cases"][0]["name"], "custom");
    assert!(result["data"]["next_offset"].is_null());
    let result = json_output(
        cargo_bin_cmd!()
            .arg("--suite")
            .arg(&suite)
            .args(["--json", "resolve", "custom"]),
        0,
    )?;
    assert_eq!(result["data"]["id"], "custom");
    assert_eq!(result["data"]["execution"], "rust");
    let result = json_output(
        cargo_bin_cmd!()
            .arg("--suite")
            .arg(&suite)
            .args(["--json", "explain", "custom"]),
        0,
    )?;
    assert_eq!(result["data"]["case"]["name"], "custom");
    let result = json_output(
        cargo_bin_cmd!()
            .arg("--suite")
            .arg(&suite)
            .args(["--json", "catalog"]),
        0,
    )?;
    assert_eq!(
        result["data"]["catalog"]["cases"]
            .as_array()
            .ok_or("missing cases")?
            .len(),
        2
    );
    assert!(result["data"]["catalog"].get("source").is_none());
    let output = cargo_bin_cmd!()
        .arg("--suite")
        .arg(&suite)
        .arg("catalog")
        .output()?;
    assert!(output.status.success());
    assert_eq!(output.stdout, fs::read(suite)?);
    Ok(())
}

#[test]
fn json_errors_cover_arguments_and_catalog_failures_without_echoing_values() -> Result {
    let result = json_output(
        cargo_bin_cmd!().args(["--json", "--invalid", "sensitive-value"]),
        2,
    )?;
    assert_eq!(result["error"]["code"], "invalid_arguments");
    assert!(!result.to_string().contains("sensitive-value"));
    let result = json_output(cargo_bin_cmd!().args(["--json", "validate"]), 1)?;
    assert_eq!(result["error"]["code"], "operation_failed");
    let root = TempDir::new()?;
    catalog(root.path())?;
    let suite = root.path().join("tests/cases.toml");
    fs::write(&suite, "sensitive-value = invalid\n")?;
    let result = json_output(
        cargo_bin_cmd!()
            .arg("--suite")
            .arg(&suite)
            .args(["--json", "validate"]),
        1,
    )?;
    assert!(!result.to_string().contains("sensitive-value"));
    let help = json_output(cargo_bin_cmd!().args(["--json", "--help"]), 0)?;
    assert!(
        help["data"]["text"]
            .as_str()
            .ok_or("missing help")?
            .contains("discover")
    );
    Ok(())
}
