#!/bin/sh
# scripts/r59_session.sh -- runs ON the NAS (nohup detached, the 脱链律: this script must
# survive the SSH channel dying; every fastboot call is timeout-wrapped; the log is the
# source of truth, the channel only probes it).
#
# The R59 join session: TWRP -> ABL -> XNULOADER -> the staging trio -> oem run ->
# dwell past the R58 record -> oem stop -> +5s (the operational law) -> logdump ->
# get_staged fallback -> (TWRP dd fallback).
#
# PREREQUISITE (the runner refuses to start without it):
#   $DIR/raphael-r59-flashboot-fw-nv.apfs  built by tools/r59_flashboot_join.py with the
#   REAL credential file (fw/network inside).  No credentials, no session -- a join
#   round with a known-wrong passphrase only buys a pre-registered failure.
#
# usage: nohup sh r59_session.sh > r59-session.log 2>&1 &

set -u
DIR="/vol1/1000/项目/hackios/r59-materials"
LOG="$DIR/../r59-session.log"
DWELL="${R59_DWELL:-2100}"          # seconds of kernel life before the stop (>= the R58 1943 s)
FB="fastboot"

say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== R59 session begin (pid $$) ==="

# ---- the prerequisite gate
[ -f "$DIR/raphael-r59-flashboot-fw-nv.apfs" ] || { say "ABORT: $DIR/raphael-r59-flashboot-fw-nv.apfs missing -- build it with tools/r59_flashboot_join.py and the real credentials first"; exit 2; }
[ -f "$DIR/r59.xnup" ] || { say "ABORT: $DIR/r59.xnup missing"; exit 2; }
say "materials: fw-nv $(ls -la "$DIR/raphael-r59-flashboot-fw-nv.apfs" | awk '{print $5}') B, xnup $(ls -la "$DIR/r59.xnup" | awk '{print $5}') B"

# ---- the device node door (the NAS fastboot law: admin is not in plugdev; re-apply, harmless if already)
NODE=$(ls /dev/bus/usb/*/* 2>/dev/null | head -0; for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor" ] && grep -q 18d1 "$d/idVendor" 2>/dev/null && basename "$(readlink -f "$d")"
done | head -3)
for b in /dev/bus/usb/*/*; do
  BUS=$(basename "$(dirname "$b")"); DEV=$(basename "$b")
  if grep -q 18d1 "/sys/bus/usb/devices/$BUS-$DEV/idVendor" 2>/dev/null || \
     grep -qs 18d1 "/sys/bus/usb/devices/$BUS-$DEV/../idVendor" 2>/dev/null; then
    chmod 0666 "$b" 2>/dev/null && say "door: chmod 0666 $b"
  fi
done

fb_wait() {  # $1 = label; wait for a fastboot device, probe only
  n=0
  while [ $n -lt 60 ]; do
    out=$(timeout 8 $FB getvar product 2>&1)
    if echo "$out" | grep -qE "product|OKAY"; then say "fb_wait($1): device answers: $(echo "$out" | head -2 | tr '\n' ' ')"; return 0; fi
    n=$((n+1)); sleep 2
  done
  say "fb_wait($1): TIMEOUT (120 s, no fastboot device)"; return 1
}

# ---- phase 1: TWRP -> ABL fastboot
say "phase 1: adb reboot bootloader"
timeout 15 adb reboot bootloader 2>&1 | say "adb: $*"
fb_wait ABL || exit 3
sleep 2

# ---- phase 2: ABL -> XNULOADER (the boot partition already carries the r54 gpu image;
#      NO fastboot flash anywhere in this session -- the single-variable law)
say "phase 2: fastboot reboot -> XNULOADER"
timeout 15 $FB reboot 2>&1 | say "fb reboot: $*"
sleep 8
fb_wait LOADER || exit 3

# ---- phase 3: the staging trio (the 启动卷律: oem ramdisk -> stage flashboot -> stage xnup -> oem run)
say "phase 3: the staging trio"
timeout 30 $FB oem ramdisk 2>&1 | say "oem ramdisk: $*"; sleep 1
say "staging the fw-nv flashboot (113 MB -- the network file is inside)"
timeout 300 $FB stage "$DIR/raphael-r59-flashboot-fw-nv.apfs" 2>&1 | say "stage fw-nv: $*"; sleep 1
say "staging the r59 xnup (24 MB, wifi_join=1 in the payload cmdline)"
timeout 180 $FB stage "$DIR/r59.xnup" 2>&1 | say "stage xnup: $*"; sleep 1

# ---- phase 4: ignition
T0=$(date +%s)
say "phase 4: oem run -- T0 = $(date +%H:%M:%S), epoch $T0"
timeout 60 $FB oem run 2>&1 | say "oem run: $*"

# ---- phase 5: the dwell (past the R58 1943 s record)
say "phase 5: dwell ${DWELL}s (past the R58 record)"
sleep "$DWELL"

# ---- phase 6: the stop + the operational law (+5 s) + the logdump
say "phase 6: oem stop, then the 5 s law, then the logdump"
timeout 60 $FB oem stop 2>&1 | say "oem stop: $*"
sleep 5
timeout 120 $FB oem logdump 2>&1 | say "oem logdump: $*"
sleep 2
timeout 300 $FB get_staged "$DIR/../r59-logdump.bin" 2>&1 | say "get_staged: $*"
ls -la "$DIR/../r59-logdump.bin" 2>/dev/null | say "captured: $*"

# ---- phase 7: the TWRP fallback read (the R54 law -- if the loader fell back to TWRP)
if timeout 12 adb get-state 2>/dev/null | grep -q device; then
  say "phase 7: a device answers adb -- TWRP fallback read of the full 64 MB partition"
  timeout 15 adb shell 'dd if=/dev/block/by-name/logdump bs=4194304 count=16 2>/dev/null | sha256sum' > "$DIR/../r59-partition.sha" 2>&1
  cat "$DIR/../r59-partition.sha" | say "partition sha: $*"
  timeout 180 adb shell 'dd if=/dev/block/by-name/logdump bs=4194304 count=16 2>/dev/null' > "$DIR/../r59-logdump-partition.bin" 2>/dev/null
  say "partition read: $(ls -la "$DIR/../r59-logdump-partition.bin" 2>/dev/null | awk '{print $5}') B"
fi

say "=== R59 session end -- judge with scripts/r59_judge.py ==="
