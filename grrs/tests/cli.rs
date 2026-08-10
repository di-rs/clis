use assert_cmd::cargo::*;
use assert_fs::fixture::FileWriteStr;
use predicates::prelude::*;

#[test]
fn file_doesnt_exists() -> Result<(), Box<dyn std::error::Error>> {
    let mut cmd = cargo_bin_cmd!("grrs");
    cmd.arg("foobar").arg("test/file/doesnt/exist");

    cmd.assert()
        .failure()
        .stderr(predicate::str::contains("could not read file"));

    Ok(())
}

#[test]
fn file_content_in_file() -> Result<(), Box<dyn std::error::Error>> {
    let file = assert_fs::NamedTempFile::new("sample.txt")?;
    file.write_str("A test\nActual content\nMore content\nAnother test")?;

    let mut cmd = cargo_bin_cmd!("grrs");
    cmd.arg("test").arg(file.path());

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("A test\nAnother test"));

    Ok(())
}

#[test]
fn empty_pattern_returns_all_file() -> Result<(), Box<dyn std::error::Error>> {
    let file = assert_fs::NamedTempFile::new("sample.txt")?;
    let file_content = "A test\nActual content\nMore content\nAnother test";
    file.write_str(file_content)?;

    let mut cmd = cargo_bin_cmd!("grrs");
    cmd.arg("").arg(file.path());

    cmd.assert()
        .success()
        .stdout(predicate::str::contains(file_content));

    Ok(())
}
