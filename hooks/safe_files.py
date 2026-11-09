"""File writes for a directory someone else controls.

The broker's data directory belongs to uid 1883 -- the uid the broker runs as
-- and the install hook writes there as root. Whoever holds uid 1883 (a
compromised broker) can plant a symbolic link at any name the hook is about
to open, so that root writes, or reads, a file of its choosing. Every helper
here therefore opens with O_NOFOLLOW, creates with O_EXCL, and never opens a
path twice.

Each write is also durable before it is relied upon: fsync of the file, then
of its directory after a rename. The migration's order -- admin secret, then
accounts file, then configuration -- only protects anything if a crash cannot
reorder it on disk.
"""
import errno
import os
import stat

READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
CREATE_FLAGS = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                | os.O_CLOEXEC)


class UnsafePath(ValueError):
    """A path is a symbolic link, or not a regular file, where one is
    expected."""


def _refuse(path, what):
    raise UnsafePath(f"{path}: {what}; refusing to follow or replace it")


def require_plain_dir(path):
    """`path` is absent or a real directory, not a link to one."""
    if os.path.lexists(path) and not stat.S_ISDIR(os.lstat(path).st_mode):
        _refuse(path, "not a directory (a symbolic link?)")


def require_regular_or_absent(path):
    if os.path.lexists(path) and not stat.S_ISREG(os.lstat(path).st_mode):
        _refuse(path, "not a regular file (a symbolic link?)")


def read_text(path):
    """The content of a regular file, or None if it does not exist."""
    try:
        fd = os.open(path, READ_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _refuse(path, "a symbolic link")
        raise
    with os.fdopen(fd, encoding="utf-8") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            _refuse(path, "not a regular file")
        return fh.read()


def _fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_fd(fd, text):
    data = text.encode("utf-8")
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


def create_exclusive(path, text, mode, owner=None):
    """Create `path`, which must not exist (not even as a dangling link),
    write `text`, fsync. `owner` = (uid, gid), applied only when root.

    Returns False when `owner` could not be applied (not root), else True.
    """
    fd = os.open(path, CREATE_FLAGS, mode)
    try:
        # The creation mode is masked by the umask; the file must have
        # exactly `mode`, a secret first of all.
        os.fchmod(fd, mode)
        owned = owner is None or os.geteuid() == 0
        if owner is not None and owned:
            os.fchown(fd, *owner)
        _write_fd(fd, text)
    except BaseException:
        os.close(fd)
        os.unlink(path)
        raise
    os.close(fd)
    _fsync_dir(os.path.dirname(path) or ".")
    return owned


def write_atomic(path, text, mode, owner=None):
    """Replace `path` with `text` through `path + '.tmp'` and a rename.

    A stale temporary -- a leftover of an interrupted run, or a link planted
    there -- is unlinked first: unlink removes the link, never its target,
    and O_EXCL then refuses anything recreated in between. A directory there
    is not the hook's and stops the write. The rename replaces `path` itself,
    so a link at `path` is replaced, not followed.

    Returns what create_exclusive() returns.
    """
    tmp = path + ".tmp"
    if os.path.lexists(tmp):
        if stat.S_ISDIR(os.lstat(tmp).st_mode):
            _refuse(tmp, "a directory")
        os.unlink(tmp)
    owned = create_exclusive(tmp, text, mode, owner)
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")
    return owned


def rewrite_in_place(path, text):
    """Truncate and rewrite an existing regular file without changing its
    inode -- for a file bind-mounted alone into a container, where a rename
    would leave the mount on the old inode."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _refuse(path, "a symbolic link")
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _refuse(path, "not a regular file")
        os.ftruncate(fd, 0)
        _write_fd(fd, text)
    finally:
        os.close(fd)
