# Private Xvfb graphical desktop

## Verified installation (25 October 2026)

The official Arch xorg-server-xvfb 21.1.25-2 package was downloaded from
the official HTTPS Arch package mirror and checked against the trusted
pacman keyring. Its detached GPG signature was valid (Andreas Radke,
RSA fingerprint ADC8A1FCC15E01D45310419E94657AB20F2A092B).

The system-wide install was blocked because pkexec reported "pkexec must be
setuid root". No privilege bypass was used. The verified Xvfb executable
was instead extracted into the owner's private, unprivileged directory:

    ~/.local/opt/open-ci-hub/xvfb-21.1.25-2/usr/bin/Xvfb

The executable SHA-256 is pinned in virtual_desktop.py:

    6584e6cf04ca1357cb50fd2feb1ad449b5f45dfe6cc61d2bbf006f752df592de

The controller prefers /usr/bin/Xvfb if installed by the OS; otherwise it
uses only the exact expected local executable when its owner, permissions,
file type and SHA-256 pass validation. Symlinks and modified bytes fail
closed.

## Start, verify and stop

From the reviewed Open CI Hub source checkout:

    python3 virtual_desktop.py check
    python3 virtual_desktop.py start --confirm
    env DISPLAY=:91 XAUTHORITY="$PWD/.local/virtual-desktop/Xauthority" xdpyinfo -display :91
    python3 virtual_desktop.py stop --confirm

The display runs independently of KDE/Wayland on :91 with resolution
1280x720x24. The private MIT-MAGIC-COOKIE is created through /usr/bin/xauth
using stdin (not process arguments) and held in a mode-0600 file. Xvfb
refuses TCP X11 connections, and starts in a transient user systemd service
with 512MiB hard memory limit, no swap, 40% CPU limit and 128-task limit.

Neither the human cursor nor KDE DISPLAY is altered. Graphical clients must
be explicitly configured to use :91. No public screen sharing, arbitrary
desktop-control agent, untrusted shell, or local browser profile is granted.

An authenticated Xvfb client and display size were verified using the
official signed executable. Actual GUI mission orchestration and an
independent X11 security review are separate acceptance gates.

## Signed package updates

This user-level installation is NOT tracked by pacman. Never replace the
executable without verifying the next official package signature and
updating the pinned SHA-256 intentionally. If administrator package
management is repaired, system-wide installation is preferable:

    sudo pacman -S --needed xorg-server-xvfb

Do not alter setuid permissions or bypass the system's authentication.
