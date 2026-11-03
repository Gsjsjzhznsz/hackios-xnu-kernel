#!/bin/sh
# r59_keeper.sh -- the ROOT door-keeper: every 5 s, re-apply 0666 on every 18d1
# USB device node (the USB permission-gate law: every re-enumeration recreates
# the node root:plugdev 0660; admin is not in plugdev; udev is UGOS-managed and
# deaf).  Runs for 90 minutes, then exits.
END=$(( $(date +%s) + 5400 ))
say() { echo "[$(date '+%F %T')] $*"; }
say "keeper begin (pid $$), until epoch $END"
while [ "$(date +%s)" -lt "$END" ]; do
  for s in /sys/bus/usb/devices/*; do
    [ -f "$s/idVendor" ] || continue
    grep -q 18d1 "$s/idVendor" 2>/dev/null || continue
    b=$(cat "$s/busnum" 2>/dev/null); n=$(cat "$s/devnum" 2>/dev/null)
    [ -n "$b" ] && [ -n "$n" ] && chmod 0666 "/dev/bus/usb/$(printf %03d "$b")/$(printf %03d "$n")" 2>/dev/null
  done
  sleep 5
done
say "keeper end (90 min window closed)"
