"""Build a least-access filesystem namespace for each autonomous browser process.

bubblewrap executes the browser with no mounts of the user's home, project
tree, credentials or desktop runtime. This is additive to Chromium sandbox
and systemd cgroup limits; network isolation is deliberately NOT claimed.
"""
from __future__ import annotations
from pathlib import Path

class SandboxUnavailable(RuntimeError):
    pass

def browser_argv(browser_folder: Path, artifacts: Path, *, plan_file=None, bwrap="/usr/bin/bwrap"):
    browser_folder, artifacts = Path(browser_folder), Path(artifacts)
    if not Path(bwrap).is_file():
        raise SandboxUnavailable("bubblewrap is required for autonomous browser jobs.")
    for folder in (browser_folder, artifacts):
        if folder.is_symlink() or not folder.is_dir():
            raise SandboxUnavailable("Browser source/artifact paths must be real directories.")
    if not (browser_folder / "browser.mjs").is_file():
        raise SandboxUnavailable("Approved browser worker missing.")
    if plan_file is not None:
        plan_file = Path(plan_file)
        if plan_file.is_symlink() or not plan_file.is_file():
            raise SandboxUnavailable("Mission file must be a real private file.")
    # No --ro-bind /, /home, /mnt, /run/user or Docker socket.
    # The browser source directory is read-only, and /artifacts is the ONLY
    # writable user filesystem mount. /tmp is an ephemeral private tmpfs.
    argv = [
        bwrap, "--die-with-parent",
        "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--clearenv",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib", "/lib64",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/home",
        "--tmpfs", "/run",
        "--dir", "/app",
        "--ro-bind", str(browser_folder.resolve()), "/app",
        "--dir", "/artifacts",
        "--bind", str(artifacts.resolve()), "/artifacts",
        "--setenv", "HOME", "/tmp",
        "--setenv", "XDG_CACHE_HOME", "/tmp",
        "--setenv", "XDG_CONFIG_HOME", "/tmp",
        "--setenv", "XDG_RUNTIME_DIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "DISPLAY", "",
        "--setenv", "WAYLAND_DISPLAY", "",
        "--setenv", "DBUS_SESSION_BUS_ADDRESS", "",
        "--setenv", "OCI_CHROMIUM_PATH", "/usr/bin/chromium",
        "--setenv", "OCI_AUTONOMY_ARTIFACT_DIR", "/artifacts",
        "--chdir", "/tmp",
    ]
    if plan_file is not None:
        argv += ["--dir", "/input",
                 "--ro-bind", str(plan_file.resolve()), "/input/plan.json",
                 "--setenv", "OCI_AUTONOMY_PLAN_PATH", "/input/plan.json"]
    return argv + ["--", "/usr/bin/node", "/app/browser.mjs"]
