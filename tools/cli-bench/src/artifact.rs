use crate::{ArtifactRecord, BenchError, BuildRecord, FileIdentity, Store};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Write},
    path::Path,
};

/// Hash a regular file using constant-size buffers; report its logical bytes.
///
/// # Errors
/// Fails on non-regular files, read errors or a size change during hashing.
pub fn fingerprint(path: &Path) -> Result<FileIdentity, BenchError> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.is_file() {
        return Err(BenchError::Evidence(
            "identity requires a regular, non-symlink file".into(),
        ));
    }
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut bytes = 0_u64;
    let mut buffer = [0_u8; 8192];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        let chunk = buffer
            .get(..count)
            .ok_or_else(|| BenchError::Evidence("invalid read length".into()))?;
        hash.update(chunk);
        bytes = bytes
            .checked_add(u64::try_from(count).map_err(|e| BenchError::Evidence(e.to_string()))?)
            .ok_or_else(|| BenchError::Evidence("file length overflow".into()))?;
    }
    if bytes != metadata.len() || bytes != file.metadata()?.len() {
        return Err(BenchError::Evidence(
            "file size changed during hashing".into(),
        ));
    }
    Ok(FileIdentity {
        sha256: format!("{:x}", hash.finalize()),
        bytes,
    })
}

/// Verify logical size and SHA-256 against a retained identity.
///
/// # Errors
/// Fails for unreadable files, invalid identities or a content mismatch.
pub fn verify_file(path: &Path, expected: &FileIdentity) -> Result<(), BenchError> {
    validate_identity(expected)?;
    if fingerprint(path)? != *expected {
        return Err(BenchError::Evidence(format!(
            "identity mismatch: {}",
            path.display()
        )));
    }
    Ok(())
}

pub fn validate_identity(identity: &FileIdentity) -> Result<(), BenchError> {
    if identity.sha256.len() != 64
        || !identity
            .sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(BenchError::Evidence("invalid SHA-256 identity".into()));
    }
    Ok(())
}

pub fn json_identity<T: serde::Serialize>(value: &T) -> Result<String, BenchError> {
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(value)?)))
}

/// Copy an executable into immutable evidence, preserving permissions and checking both copies.
/// Unknown prebuilt provenance remains `None`; this never invokes a compiler.
///
/// # Errors
/// Fails on invalid build provenance, changed content, conflicting records or I/O errors.
pub fn register_binary(
    path: &Path,
    build: Option<BuildRecord>,
    store: &Store,
) -> Result<ArtifactRecord, BenchError> {
    if let Some(record) = &build {
        record.validate()?;
    }
    let identity = fingerprint(path)?;
    let id = json_identity(&(1_u32, &identity, &build))?;
    let record = ArtifactRecord {
        schema_version: 1,
        id,
        file: identity,
        build,
    };
    let _lock = store.transaction_lock()?;
    let destination = store.artifact_path(&record);
    if destination.exists() {
        store.verify_artifact(&record)?;
        return Ok(record);
    }
    let staging = crate::store::unique_directory(&store.root().join("artifacts"), "pending")?;
    let copy = staging.join("executable");
    let mut source = File::open(path)?;
    let mut output = File::options().write(true).create_new(true).open(&copy)?;
    std::io::copy(&mut source, &mut output)?;
    output.flush()?;
    output.set_permissions(source.metadata()?.permissions())?;
    output.sync_all()?;
    verify_file(path, &record.file)?;
    verify_file(&copy, &record.file)?;
    crate::store::atomic_json(&staging.join("build.json"), &record)?;
    File::open(&staging)?.sync_all()?;
    std::fs::rename(
        &staging,
        destination
            .parent()
            .ok_or_else(|| BenchError::Evidence("missing artifact parent".into()))?,
    )?;
    File::open(store.root().join("artifacts"))?.sync_all()?;
    Ok(record)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
        if condition {
            Ok(())
        } else {
            Err(message.into())
        }
    }
    #[test]
    fn tampered_copy_is_rejected() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let path = root.path().join("binary");
        std::fs::write(&path, b"abc")?;
        let expected = FileIdentity {
            sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad".into(),
            bytes: 3,
        };
        verify_file(&path, &expected)?;
        std::fs::write(&path, b"abd")?;
        require(
            verify_file(&path, &expected).is_err(),
            "tampered bytes were accepted",
        )?;
        Ok(())
    }
    #[test]
    fn fingerprints_logical_contents() -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let path = root.path().join("file");
        std::fs::write(&path, b"abc")?;
        require(
            fingerprint(&path)?
                == FileIdentity {
                    sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
                        .into(),
                    bytes: 3,
                },
            "wrong hash/size",
        )?;
        Ok(())
    }
    #[test]
    fn registering_prebuilt_preserves_unknown_provenance_and_refuses_tampered_reuse()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let source = root.path().join("source");
        std::fs::write(&source, b"abc")?;
        let store = Store::open(&root.path().join("evidence"))?;
        let first = register_binary(&source, None, &store)?;
        require(
            first.build.is_none(),
            "compiler attributed to a prebuilt file",
        )?;
        require(
            register_binary(&source, None, &store)? == first,
            "identical registration changed identity",
        )?;
        std::fs::write(store.artifact_path(&first), b"tampered")?;
        require(
            register_binary(&source, None, &store).is_err(),
            "tampered artifact overwritten or reused",
        )?;
        Ok(())
    }
}
