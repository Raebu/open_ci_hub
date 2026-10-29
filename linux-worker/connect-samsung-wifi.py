#!/usr/bin/env python3
"""Provision the owner's existing Raeburn Wi-Fi profile on the Samsung Book Go.

Runs on the trusted Razer, NOT the Samsung. Gets the WPA-PSK via NetworkManager
in memory and streams it directly into a protected profile on the already
authenticated Tailscale SSH connection. NEVER prints, logs or writes the
secret on the Razer or includes it in a command argument.

Does not remove the S22 USB tether, alter existing routes, rewrite boot files
or install drivers. Exact destination is create-only and owner-controlled.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import uuid

PEER = "root@100.77.117.120"
PROFILE = "/etc/NetworkManager/system-connections/raeburn-wifi.nmconnection"
SSID = "Raeburn"
NAME = "Raeburn Wi-Fi"
MODEL = "Samsung Galaxy Book Go"

PRECHECK = (
    "set -eu; "
    "[ \"$(hostname)\" = samsung-book-go ]; "
    "[ \"$(tr -d '\\000' < /proc/device-tree/model)\" = 'Samsung Galaxy Book Go' ]; "
    "test \"$(findmnt -no SOURCE /)\" = /dev/sda2; "
    "[ -d /etc/NetworkManager/system-connections ]; "
    "[ -d /sys/class/net/wlan0 ]; "
    "test ! -e " + PROFILE
)
RECEIVE = (
    PRECHECK + "; "
    "umask 077; "
    "cat > " + PROFILE + "; "
    "chmod 600 " + PROFILE + "; "
    "nmcli connection load " + PROFILE + " >/dev/null; "
    "nmcli --wait 35 connection up id 'Raeburn Wi-Fi' ifname wlan0 >/dev/null; "
    "printf 'Wi-Fi profile activated: '; "
    "nmcli -g GENERAL.STATE device show wlan0; "
    "printf 'WLAN IPv4: '; ip -4 -o address show dev wlan0 | "
    "awk '{print $4}'"
)


class Refused(ValueError):
    pass


def get_owner_psk(run=subprocess.run):
    # Avoid shell expansion, env storage, disk writes, stdout, diagnostic logs.
    data = run(
        ["nmcli", "--show-secrets", "-g", "802-11-wireless-security.psk",
         "connection", "show", SSID],
        capture_output=True, text=True, timeout=7, check=False,
    )
    value = data.stdout.strip("\r\n") if data.returncode == 0 else ""
    if not (8 <= len(value) <= 63 or (
            len(value) == 64 and re.fullmatch("[a-fA-F0-9]{64}", value))):
        raise Refused("Razer's existing Wi-Fi PSK was unavailable or invalid.")
    if any(x in value for x in ("\n", "\r", "\x00")):
        raise Refused("Malformed Wi-Fi credential.")
    return value


def make_keyfile(secret):
    if not isinstance(secret, str) or not (8 <= len(secret) <= 64):
        raise Refused("Wi-Fi PSK invalid.")
    # NetworkManager keyfile; controlled SSID, name, interface and URI only.
    return (
        "[connection]\n"
        f"id={NAME}\n"
        f"uuid={uuid.uuid4()}\n"
        "type=wifi\ninterface-name=wlan0\n"
        "autoconnect=true\nautoconnect-priority=30\n"
        "[wifi]\nmode=infrastructure\n"
        f"ssid={SSID}\n"
        "[wifi-security]\nkey-mgmt=wpa-psk\n"
        f"psk={secret}\n"
        "[ipv4]\nmethod=auto\nroute-metric=600\n"
        "[ipv6]\nmethod=auto\nroute-metric=600\n"
    ).encode("utf8")


def run(*,confirm=False,invoke=subprocess.run):
    if not confirm:
        raise Refused("Explicit --confirm required; no network change made.")
    if os.geteuid() == 0:
        raise Refused("Run as the ordinary Razer user, never root.")
    # Independent identity check before any PSK is accessed or sent.
    state = invoke(["tailscale", "ssh", PEER, PRECHECK],
                   stdin=subprocess.DEVNULL, capture_output=True,
                   timeout=16, check=False)
    if state.returncode != 0:
        raise Refused(
            "Samsung identity, root filesystem, wlan0 or unused profile path "
            "could not be verified; did not send Wi-Fi secret.")
    key = get_owner_psk(run=invoke)
    profile = make_keyfile(key)
    # The remote shell command itself never contains the password.
    outcome = invoke(["tailscale", "ssh", PEER, RECEIVE],
                     input=profile, capture_output=True,
                     timeout=55, check=False)
    if outcome.returncode:
        # A failed connection may leave an owner-private profile. Preserve it
        # for inspection; never overwrite or automatically retry a secret.
        raise Refused("Samsung network activation failed; "
                      "protected profile may need inspection. No automatic retry.")
    return {"device":MODEL,"ssid":SSID,"profile_created":True,
            "wifi_password_disclosed":False,
            "usb_tether_left_connected":True,
            "wifi_activation":"command_succeeded_pending_independent_test"}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--confirm",action="store_true")
    args=p.parse_args(argv)
    try:
        print(run(confirm=args.confirm))
        return 0
    except (Refused, OSError, subprocess.TimeoutExpired) as exc:
        print("Samsung Wi-Fi provisioning blocked: "+str(exc)[:230],
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
