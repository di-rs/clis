"""Read Darwin's binary ACL representation without resolving principal names."""

import ctypes
import errno
import os
from pathlib import Path


def read_acl(path: Path) -> bytes:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.acl_get_file.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_size.argtypes = [ctypes.c_void_p]
    libc.acl_size.restype = ctypes.c_ssize_t
    libc.acl_copy_ext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
    libc.acl_copy_ext.restype = ctypes.c_ssize_t
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int

    # ACL_TYPE_EXTENDED and ACL_FIRST_ENTRY from Darwin's <sys/acl.h>.
    handle = libc.acl_get_file(os.fsencode(path), 0x00000100)
    if not handle:
        code = ctypes.get_errno()
        # Darwin also reports ENOENT when the file exists without an ACL.
        # stat must succeed so a missing fixture is never treated as an empty ACL.
        if code == errno.ENOENT:
            path.stat()
            return b""
        raise OSError(code, os.strerror(code), str(path))
    try:
        entry = ctypes.c_void_p()
        if libc.acl_get_entry(handle, 0, ctypes.byref(entry)) != 0:
            code = ctypes.get_errno()
            # A valid ACL with no first entry is empty on Darwin.
            if code == errno.EINVAL:
                return b""
            raise OSError(code, os.strerror(code), str(path))
        size = int(libc.acl_size(handle))
        if size < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(path))
        storage = ctypes.create_string_buffer(size)
        written = int(libc.acl_copy_ext(storage, handle, size))
        if written < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(path))
        return storage.raw[:written]
    finally:
        libc.acl_free(handle)
