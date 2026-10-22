use std::{
    fs::{File, Permissions},
    io,
    os::unix::fs::{MetadataExt, PermissionsExt, fchown},
    path::Path,
};

#[cfg(target_os = "macos")]
type Acl = Vec<exacl::AclEntry>;
#[cfg(target_os = "linux")]
type Acl = Option<Vec<u8>>;
#[cfg(not(any(target_os = "macos", target_os = "linux")))]
type Acl = ();

pub(super) struct Security {
    uid: u32,
    gid: u32,
    permissions: Permissions,
    acl: Acl,
}

impl Security {
    pub(super) fn read(path: &Path, file: &File) -> io::Result<Self> {
        let metadata = file.metadata()?;
        Ok(Self {
            uid: metadata.uid(),
            gid: metadata.gid(),
            permissions: metadata.permissions(),
            acl: read_acl(path, file)?,
        })
    }

    pub(super) fn apply(&self, path: &Path, file: &File) -> io::Result<()> {
        let metadata = file.metadata()?;
        if (metadata.uid(), metadata.gid()) != (self.uid, self.gid) {
            fchown(file, Some(self.uid), Some(self.gid))?;
        }
        // Changing ownership can clear mode bits; installing the ACL comes last.
        file.set_permissions(self.permissions.clone())?;
        write_acl(path, file, &self.acl)?;
        let actual = Self::read(path, file)?;
        if actual.uid != self.uid
            || actual.gid != self.gid
            || actual.permissions.mode() != self.permissions.mode()
            || actual.acl != self.acl
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "cannot preserve destination ownership, permissions, and ACL",
            ));
        }
        Ok(())
    }
}

pub(super) fn make_private(path: &Path, file: &File) -> io::Result<()> {
    file.set_permissions(Permissions::from_mode(0o600))?;
    // A directory may give new files an inherited ACL, even with mode 0600.
    write_acl(path, file, &Acl::default())
}

pub(super) fn make_private_directory(path: &Path) -> io::Result<()> {
    let directory = File::open(path)?;
    directory.set_permissions(Permissions::from_mode(0o700))?;
    // Clear directory access before creating a file: an already-open empty-file
    // descriptor would otherwise retain access after its inherited ACL is removed.
    write_acl(path, &directory, &Acl::default())
}

#[cfg(target_os = "macos")]
fn read_acl(path: &Path, _file: &File) -> io::Result<Acl> {
    exacl::getfacl(path, None)
}

#[cfg(target_os = "macos")]
fn write_acl(path: &Path, _file: &File, acl: &Acl) -> io::Result<()> {
    exacl::setfacl(&[path], acl, None)
}

#[cfg(target_os = "linux")]
fn read_acl(_path: &Path, file: &File) -> io::Result<Acl> {
    use xattr::FileExt;
    file.get_xattr("system.posix_acl_access")
}

#[cfg(target_os = "linux")]
fn write_acl(_path: &Path, file: &File, acl: &Acl) -> io::Result<()> {
    use xattr::FileExt;
    if let Some(acl) = acl {
        file.set_xattr("system.posix_acl_access", acl)
    } else if file.get_xattr("system.posix_acl_access")?.is_some() {
        file.remove_xattr("system.posix_acl_access")
    } else {
        Ok(())
    }
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn read_acl(_path: &Path, _file: &File) -> io::Result<Acl> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "ACL preservation is unsupported on this platform",
    ))
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn write_acl(path: &Path, file: &File, _acl: &Acl) -> io::Result<()> {
    read_acl(path, file)
}
