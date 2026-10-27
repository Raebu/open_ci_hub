#!/usr/bin/env python3
"""Build a three-line, keyboard-only DTB fix for the June 2026 Galaxy Book Go test image.

No writes to block devices. Refuses unknown or altered source DTB.
Upstream basis:
https://github.com/xbill9/linux-arm-adventures/blob/main/galaxy-book-go/scripts/gbgo-dtb-patch
"""
import difflib
import hashlib
from pathlib import Path
import subprocess
import sys

EXPECTED_DTB_SHA256 = "8a89b6efa5d584411fbc21585bdbea4062017fab8d797e65ab20a103a8c6d56a"
ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".local" / "galaxy-book-go"
SOURCE = Path("/run/media/theraeburngroup/bootpart/dtb-6.18.34-stb-qc7+/sc7180-samsung-galaxy-book-go.dtb")
DTC = WORK / "dtc-source" / "dtc"
OUT = WORK / "samsung-keyboard-gpio104.dtb"
ORIG_DTS = WORK / "samsung-current.dts"
PATCHED_DTS = WORK / "samsung-keyboard-gpio104.dts"

if not SOURCE.is_file() or not DTC.is_file():
    sys.exit("Original SanDisk DTB or local dtc binary is missing; no changes made.")
digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
if digest != EXPECTED_DTB_SHA256:
    sys.exit("Original DTB checksum differs from the reviewed June-2026 SanDisk image. No changes made.")
subprocess.run([str(DTC), "-q", "-I", "dtb", "-O", "dts", "-o", str(ORIG_DTS), str(SOURCE)], check=True)
raw = ORIG_DTS.read_text()
old_irq = "interrupts-extended = <0x3c 0x30 0x08>;"
new_irq = "interrupts-extended = <0x3c 0x68 0x08>;"
if raw.count(old_irq) != 1:
    sys.exit("Unexpected keyboard IRQ cell pattern; no changes made.")
left_marker = "\t\t\thid-keyboard-default-state {"
right_marker = "\n\t\t\t};"
if raw.count(left_marker) != 1:
    sys.exit("Keyboard pin control node missing/duplicated.")
before, node_tail = raw.split(left_marker, 1)
if right_marker not in node_tail:
    sys.exit("Keyboard pin control node was not terminated.")
node, after = node_tail.split(right_marker, 1)
if node.count('pins = "gpio48";') != 1 or node.count("bias-disable;") != 1:
    sys.exit("Keyboard interrupt pin control differs from reviewed draft.")
fixed = raw.replace(old_irq, new_irq).replace(
    left_marker + node + right_marker,
    left_marker + node.replace('pins = "gpio48";', 'pins = "gpio104";').replace(
        "bias-disable;", "bias-pull-up;") + right_marker,
)
diff = list(difflib.unified_diff(raw.splitlines(), fixed.splitlines(), fromfile="factory-device-tree", tofile="gpio104-only", lineterm=""))
additions = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
deletions = [line for line in diff if line.startswith("-") and not line.startswith("---")]
if len(additions) != 3 or len(deletions) != 3:
    sys.exit("More than the three reviewed keyboard properties would change: refusing.")
PATCHED_DTS.write_text(fixed)
subprocess.run([str(DTC), "-q", "-I", "dts", "-O", "dtb", "-o", str(OUT), str(PATCHED_DTS)], check=True)
assert OUT.is_file() and OUT.stat().st_size > 80000
print("\n".join(diff))
print("Source SHA256", digest)
print("Patched SHA256", hashlib.sha256(OUT.read_bytes()).hexdigest())
print("Patched DTB staged at", OUT)
print("NO USB files have been modified. Stage requires separate privileged installation.")
