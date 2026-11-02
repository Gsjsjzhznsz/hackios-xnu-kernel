#!/bin/sh
# scripts/r59_session.sh (v2) -- runs ON the NAS (nohup detached, the 脱链律).
#
# R59 join session, attempt 2.  Attempt 1 (2026-11-01) taught the staging-failure
# law: `oem ramdisk` re-enumerates the loader's USB, the fresh /dev node comes back
# root:plugdev 0660 (admin not in plugdev), the 113 MB `fastboot stage` then fails
# IN ~1 s (physically impossible transfer time) and the say-pipeline "$*" artifact
# swallowed the error text; the loader's DRAM staging buffer still held the R56
# fw-nv from the R58 session (warm-reset survivor, the same law that preserved the
# 4 MB log), so the kernel booted with a firmware set that has NO fw/network and
# join_given() said "no fw/network in the small image" (HR15-3).
#
# v2 laws, all enforced here:
#   L1  the door (chmod 0666 on every 18d1 node) is re-applied BEFORE EVERY fastboot
#       call, not once at the top;
#   L2  every fastboot call's REAL output is captured and logged (rc + OKAY/FAIL),
#       never through a pipeline-into-say artifact;
#   L3  a stage is only accepted when its output carries OKAY; on failure retry x3
#       with 6 s spacing (the keeper law re-opens the door between passes), then
#       ABORT the session -- a silent stale-buffer merge must be impossible;
#   L4  no fastboot flash anywhere (the single-variable law: boot partition keeps
#       the r54 gpu image; the session only stages).
#
# usage: nohup sh r59_session.sh > r59-session.log 2>&1 </dev/null &

set -u
DIR="/vol1/1000/项目/hackios/r59-materials"
DWELL="${R59_DWELL:-2100}"          # seconds of kernel life before the stop (>= the R58 1943 s)

say() { echo "[$(date +%H:%M:%S)] $*"; }

door() {  # the NAS fastboot door: re-apply 0666 on every 18d1 node (harmless if open)
  for s in /sys/bus/usb/devices/*; do
    [ -f "$s/idVendor" ] || continue
    grep -q 18d1 "$s/idVendor" 2>/dev/null || continue
    b=$(cat "$s/busnum" 2>/dev/null); n=$(cat "$s/devnum" 2>/dev/null)
    [ -n "$b" ] && [ -n "$n" ] && chmod 0666 "/dev/bus/usb/$(printf %03d "$b")/$(printf %03d "$n")" 2>/dev/null
  done
}

fb() {  # L1+L2: door, then fastboot with REAL output captured; $1=label, rest = args
  label=$1   # the label IS fastboot's first real argument: `fb oem ramdisk` must
             # run `fastboot oem ramdisk` -- the shift here ate the first real arg
             # and every fb call (oem ramdisk / reboot / oem run / oem stop) ran
             # the WRONG command (rc=1 in ~0s, "transients" that never were)
             # until the R59.5 fires cornered it
  door
  t0=$(date +%s)
  out=$(timeout "$FBTO" fastboot "$@" 2>&1); rc=$?
  dt=$(( $(date +%s) - t0 ))
  say "fb $label: rc=$rc ${dt}s :: $(echo "$out" | grep -E "OKAY|FAIL|error|finished|Finished" | tail -2 | tr '\n' ' ')"
  return $rc
}

fb_stage() {  # L3: stage with the OKAY gate and x3 retry
  label=$1; file=$2
  n=1
  while [ $n -le 3 ]; do
    door
    t0=$(date +%s)
    out=$(timeout 300 fastboot stage "$file" 2>&1); rc=$?
    dt=$(( $(date +%s) - t0 ))
    if [ $rc -eq 0 ] && echo "$out" | grep -q OKAY; then
      say "stage $label: ACCEPTED (try $n, rc=$rc, ${dt}s): $(echo "$out" | tail -1)"
      return 0
    fi
    say "stage $label: REJECTED try $n (rc=$rc, ${dt}s): $(echo "$out" | tail -1)"
    n=$((n + 1)); sleep 6
  done
  say "ABORT: $label staging failed 3x -- a stale-buffer merge must be impossible (the attempt-1 law)"
  exit 4
}

fb_wait() {  # $1=label; poll for a fastboot device (door applied each pass)
  n=0
  while [ $n -lt 60 ]; do
    door
    out=$(timeout 8 fastboot getvar product 2>&1)
    if echo "$out" | grep -qE "product"; then
      say "fb_wait($1): $(echo "$out" | head -1)"
      return 0
    fi
    n=$((n + 1)); sleep 2
  done
  say "fb_wait($1): TIMEOUT (600 s)"; return 1
}

FBTO=30
say "=== R59 session v2 begin (pid $$) ==="

# ---- the prerequisite gate (the xnup path is an env knob: R59_XNUP defaults to
#      $DIR/r59.xnup; a later round stages its own payload honestly named, e.g.
#      R59_XNUP=$DIR/r60.xnup -- the fw-nv (the credential carrier) is round-agnostic)
XNUP="${R59_XNUP:-$DIR/r59.xnup}"
[ -f "$DIR/raphael-r59-flashboot-fw-nv.apfs" ] || { say "ABORT: fw-nv missing"; exit 2; }
[ -f "$XNUP" ] || { say "ABORT: xnup missing at $XNUP"; exit 2; }
say "materials: fw-nv $(ls -la "$DIR/raphael-r59-flashboot-fw-nv.apfs" | awk '{print $5}') B, xnup $(ls -la "$XNUP" | awk '{print $5}') B ($XNUP)"

# ---- phase 1: TWRP -> ABL fastboot
FBTO=15
if adb get-state >/dev/null 2>&1; then
  say "phase 1: adb reboot bootloader"
  out=$(timeout 15 adb reboot bootloader 2>&1); say "adb: rc=$? :: $out"
else
  say "phase 1: no adb device (already in a bootloader state)"
fi
fb_wait ABL || exit 3
sleep 2

# ---- phase 2: ABL -> XNULOADER (NO fastboot flash anywhere -- L4)
#     the entry-state law (attempt-2 手工指挥 + the R59.5 fires): the reboot
#     transient window (~0 s rc=1 failures while ABL settles) is retried x3/3 s,
#     and a session re-fired while the phone is ALREADY in XNULOADER must skip
#     the reboot -- `fastboot reboot` inside the loader would walk the phone back
#     OUT of it
FBTO=15
if timeout 10 fastboot getvar product 2>&1 | grep -q xnuloader; then
  say "phase 2: already in XNULOADER (the entry-state law) -- reboot skipped"
else
  rb=1
  while [ $rb -le 3 ]; do
    if fb reboot; then break; fi
    say "reboot try $rb failed (the transient law; the fb line has the real rc) -- retry in 3 s"
    rb=$((rb + 1)); sleep 3
  done
  [ $rb -le 3 ] || exit 3
  sleep 8
  fb_wait LOADER || exit 3
fi

# ---- phase 3: the staging trio (启动卷律: oem ramdisk -> stage fw-nv -> stage xnup)
FBTO=30
say "phase 3: the staging trio (v2: OKAY-gated, retried)"
rm_try=1
while [ $rm_try -le 3 ]; do
  if fb oem ramdisk; then break; fi
  say "oem ramdisk try $rm_try failed (the settling-window law) -- retry in 3 s"
  rm_try=$((rm_try + 1)); sleep 3
done
[ $rm_try -le 3 ] || exit 3
sleep 2                          # the attempt-1 law: let any re-enumeration settle
fb_stage fw-nv "$DIR/raphael-r59-flashboot-fw-nv.apfs" || exit 4
fb_stage xnup "$XNUP" || exit 4

# ---- phase 4: ignition
FBTO=60
T0=$(date +%s)
say "phase 4: oem run -- T0 = $(date +%H:%M:%S), epoch $T0"
fb oem run || say "oem run rc=$? (the loader may drop the bus as the kernel takes over -- not fatal by itself)"

# ---- phase 5: the dwell (past the R58 1943 s record)
say "phase 5: dwell ${DWELL}s"
sleep "$DWELL"

# ---- phase 6: the stop + the 5 s law + the logdump
FBTO=60
say "phase 6: oem stop, +5 s, then the logdump"
fb oem stop; sleep 5
FBTO=120
fb oem logdump
sleep 2
FBTO=300
out=$(timeout 300 fastboot get_staged "$DIR/../r59-logdump.bin" 2>&1); rc=$?
say "get_staged: rc=$rc :: $(echo "$out" | tail -1) :: $(ls -la "$DIR/../r59-logdump.bin" 2>/dev/null | awk '{print $5}') B"

# ---- phase 7: the TWRP fallback read (the R54 law)
sleep 10
if adb get-state >/dev/null 2>&1; then
  say "phase 7: TWRP answers adb -- the full 64 MB partition read"
  out=$(timeout 15 adb shell 'dd if=/dev/block/by-name/logdump bs=4194304 count=16 2>/dev/null | sha256sum'); say "partition sha: $out"
  timeout 300 adb shell 'dd if=/dev/block/by-name/logdump bs=4194304 count=16 2>/dev/null' > "$DIR/../r59-logdump-partition.bin" 2>/dev/null
  say "partition read: $(ls -la "$DIR/../r59-logdump-partition.bin" 2>/dev/null | awk '{print $5}') B"
else
  say "phase 7: no adb device (loader keeps the bus) -- the get_staged capture stands"
fi

say "=== R59 session v2 end -- judge with scripts/r59_judge.py ==="
