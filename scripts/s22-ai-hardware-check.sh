#!/data/data/com.termux/files/usr/bin/bash
# Read-only Samsung Galaxy S22 Ultra hardware/AI-host inventory.
# Does not list apps, user files, photos, accounts or hardware identifiers.
# Never requests root, removes content or transmits information.
set -u
printf '%s\n' '=== RAEBURN S22 AI HARDWARE AUDIT ==='
printf '%s\n' 'DEVICE:'
printf 'Model: '; getprop ro.product.model 2>/dev/null
printf 'SoC: '; getprop ro.soc.model 2>/dev/null
printf 'Chipset platform: '; getprop ro.board.platform 2>/dev/null
printf 'Android version: '; getprop ro.build.version.release 2>/dev/null
printf 'CPU architecture: '; getprop ro.product.cpu.abi 2>/dev/null

printf '%s\n' 'PHYSICAL MEMORY:'
awk '/^(MemTotal|MemAvailable|MemFree|SwapTotal|SwapFree):/{printf "%s %.2f GiB\n",$1,$2/1048576}' /proc/meminfo 2>/dev/null
printf 'CPU cores: '; getconf _NPROCESSORS_ONLN 2>/dev/null || printf '%s\n' 'not available'

printf '%s\n' 'STORAGE:'
df -h "$HOME" 2>/dev/null | tail -1
if [ -d /sdcard ]; then
  printf 'Shared storage: '; df -h /sdcard 2>/dev/null | tail -1
fi

printf '%s\n' 'GPU / GRAPHICS CAPABILITIES:'
printf 'GPU family (SM-S908B): Samsung Xclipse 920 if official Exynos variant\n'
printf 'EGL driver: '; getprop ro.hardware.egl 2>/dev/null
printf 'Vulkan driver property: '; getprop ro.hardware.vulkan 2>/dev/null
printf 'Vulkan Android feature: '
if command -v pm >/dev/null 2>&1; then
  pm list features 2>/dev/null | grep -E '^feature:android.hardware.vulkan.(level|version)=' | tr '\n' ' ' || true
fi
printf '\n'
printf '%s\n' 'Vulkan driver libraries:'
ls /vendor/lib64/hw/vulkan.*.so /system/vendor/lib64/hw/vulkan.*.so 2>/dev/null | sed 's#^.*/##' | head -5 || true
if command -v vulkaninfo >/dev/null 2>&1; then
  printf '%s\n' 'Vulkaninfo (if locally installed):'
  if command -v timeout >/dev/null 2>&1; then
    timeout 8 vulkaninfo --summary 2>/dev/null | grep -Ei '(deviceName|deviceType|apiVersion|driverName)' | head -12 || true
  fi
fi
printf '%s\n' 'BATTERY (IF TERMUX:API EXISTS):'
if command -v termux-battery-status >/dev/null 2>&1; then
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 termux-battery-status 2>/dev/null | grep -E '"(percentage|status|plugged|temperature|health)"' | head -7 || true
  fi
fi
printf '%s\n' 'TERMUX / AI TOOLS:'
for program in python3 clang cmake llama-cli llama-server; do
  if command -v "$program" >/dev/null 2>&1; then printf '%s installed\n' "$program"; else printf '%s missing\n' "$program"; fi
done
printf 'Open CI worker service: '
if command -v sv >/dev/null 2>&1; then sv status oci-edge-worker 2>/dev/null || printf '%s\n' 'not available'; else printf '%s\n' 'not installed'; fi
printf '%s\n' '=== END S22 AUDIT (NO FILES CHANGED) ==='
