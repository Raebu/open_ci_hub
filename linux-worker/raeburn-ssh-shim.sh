#!/bin/sh
# Tailscale SSH's own transport and host-key verification remain active.
# The user-systemd mount namespace misrepresents root ownership of the
# globally included /etc/ssh/systemd symlink; do not parse global SSH config.
exec /usr/bin/ssh -F /dev/null "$@"
