"""Cross-process setup lock; kernel releases it after interruption or process exit."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def setup_lock(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / 'runner-setup.lock'
    if directory.is_symlink() or path.is_symlink():
        raise RuntimeError('Refusing runner setup lock through a symlink.')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('Another runner setup is in progress. Finish it before retrying.') from None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another runner setup is in progress. Finish it before retrying.') from None
        locked = True
        yield
    finally:
        if locked:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        # Do not unlink: replacing the inode lets concurrent processes bypass locks.
