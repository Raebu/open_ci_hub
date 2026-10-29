#!/bin/sh
# Samsung Galaxy Book Go SC7180: start Qualcomm modem and its built-in WLAN.
# Runs after rmtfs + tqftpserv; the two exact signed firmwares were independently
# SHA256-checked against a known working driver store image and tested LIVE.
# Does not change GRUB, kernel, internal storage or the primary route.
set -eu

ROOT=/sys/class/remoteproc/remoteproc0
BASE=/lib/firmware/qcom/sc7180/samsung/galaxy-book-go
MODEL=/proc/device-tree/model

check() {
    [ "$(tr -d '\000' < "$MODEL")" = "Samsung Galaxy Book Go" ] || {
        echo "Refusing unapproved hardware" >&2; exit 1;
    }
    [ "$(cat "$ROOT/name")" = modem ] || {
        echo "Refusing unexpected modem device" >&2; exit 1;
    }
    (cd "$BASE" && sha256sum -c - >/dev/null 2>&1 <<'HASHES'
a4016412a5071a95934b0e9db343d51dd8b1cda1252f3b6baae09b34356f02f2  qcmpss7180_nm.mbn
7688eea1c13715289f58c8d379b9f73b81d072415ac141b10b4732d5059f7f4b  wlanmdsp.mbn
HASHES
    ) || {
        echo "Samsung firmware hashes differ from verified originals" >&2; exit 1;
    }
}

wifi_present() {
    # Uses PHY device path, not a fragile interface name.
    for d in /sys/class/ieee80211/phy*/device/net/*; do
        [ -e "$d" ] && return 0
    done
    return 1
}

wait_for_wifi() {
    n=0
    while [ "$n" -lt 25 ]; do
        wifi_present && return 0
        sleep 2
        n=$((n + 1))
    done
    return 1
}

check
if wifi_present; then
    echo "Samsung Wi-Fi interface already present; no modem restart"
    exit 0
fi
case "$(cat "$ROOT/state")" in
    offline)
        echo "Starting verified Samsung Qualcomm modem"
        echo start > "$ROOT/state" ;;
    running)
        echo "Modem already running; awaiting WLAN DSP" ;;
    *)
        echo "Modem in unexpected state; refusing restart" >&2
        exit 1 ;;
esac
if wait_for_wifi; then
    echo "Samsung Wi-Fi PHY ready"
    exit 0
fi
# Qualcomm firmware can race its TFTP service during boot.
# A single restart is proven safe on this exact Samsung and permits recovery.
echo "Wi-Fi missing after initial startup; retrying modem exactly once"
if [ "$(cat "$ROOT/state")" = running ]; then
    echo stop > "$ROOT/state"
fi
sleep 2
echo start > "$ROOT/state"
if wait_for_wifi; then
    echo "Samsung Wi-Fi PHY recovered after single retry"
    exit 0
fi
echo "Samsung Wi-Fi unavailable after guarded boot recovery" >&2
exit 1
