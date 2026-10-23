#!/bin/bash
# tools/r10-flash-build.sh -- pack the r10 SpringBoard boot image for raphael (K20 Pro, hackios).
#
# R10 = the clean-screen round.  R9's doorbell patch (27 -> 19) brought the chain
# all the way up: the user's report from the device -- the home screen renders
# under the -v console, and once iOS stands the loader's fastboot goes mute (USB
# taken over).  Full boot.  The ask for R10: the desktop, unpainted.
#
#   CMDLINE  : "-v" dropped from the payload's cmdline.  boot_args.video.v_display
#              flips to 1 (graphics mode) -- the kernel keeps its text off the
#              panel, the way a non-verbose iPhone boots.  The kernel's and
#              launchd's logs stop painting; everything they would have printed
#              still lands in the channels that never pass through a console:
#              the heartbeat counter and the kernel's log ring (oem log, for the
#              days the boot does not come up and fastboot still answers).
#   LOADER  0.10: the "debug=0x8 " prefix is gone from the base cmdline (raphael
#              has no UART; the bit fed only the console overlay).  New: the
#              panel is handed over BLACK -- without a verbose console to scroll
#              them away, the loader's own last words (and the firmware's) would
#              sit on the screen for the whole launchd boot until SpringBoard's
#              first frame covers them.  Everything else is R9's loader
#              untouched: oem irqtest, the GIC set, group repair, the ladder.
#   KERNEL  : byte-identical to R9's (the doorbell patch, tools/r9-kernel-patch.py,
#              INTID 27 -> 19).  r1/r2/r4 verified bytes (shim/FD/DTB) untouched.
#
# usage: tools/r10-flash-build.sh [tree]        (default: this repository)
#   ROOTAPFS=... FLASHBOOT=... OUT=...         overrides
#
# Phone side (Termux, after fastboot flash userdata + reboot to bootloader):
#   fastboot boot  raphael-xnu-r10-flash.img
#   fastboot oem  ramdisk
#   fastboot stage raphael-r10-flashboot.apfs
#   fastboot oem  run                          # black for a while, then the desktop
set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL_SRC=$TREE/prebuilt/mach.development.bcm2837
KERNEL=$TREE/prebuilt/mach.development.bcm2837.r9
OUT=${OUT:-$TREE/userland/raphael-xnu-r10-flash.img}
CMDLINE='ufs=2 bootcpu=7 root_apfs=ufs_userdata'

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL_SRC" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r10-flash-build: missing $f"; exit 1; }
done

# The kernel this build ships is R9's doorbell patch, unchanged: retarget the
# vtimer INTID 27 -> 19 (byte-anchored, capstone-verified on both ends, exactly
# 4 words / 6 bytes -- see tools/r9-kernel-patch.py).
python3 -c "import capstone" 2>/dev/null \
        || python3 -m pip install --quiet --break-system-packages capstone \
        || python3 -m pip install --quiet --user --break-system-packages capstone
python3 tools/r9-kernel-patch.py "$KERNEL_SRC" "$KERNEL"

# The loader is the other piece this build owns: compile it (clang + lld-link must be in PATH).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r10-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
python3 - "$LOADER" <<'PY'
import sys
d = open(sys.argv[1], 'rb').read()
for feat in (b'oem gic', b'oem sgi', b'oem peek', b'NXSB at +32',
             b'as the firmware left it', b'never showed up as pending', b'oem gicset',
             b'oem gicgroup', b'group repair', b'pre-arm before CPU_ON',
             b'at the hung verdict',
             b'oem irqtest', b'DELIVERS as a Group 1 NS IRQ', b'R10 branch',
             b'the readback reads 0',
             b'R10 clean screen round', b'screen cleared for the handoff'):
    if feat not in d:
        print(f'r10-flash-build: loader lacks the marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
if b'debug=0x8' in d:
    print('r10-flash-build: loader still carries debug=0x8 -- the R10 base cmdline is not clean')
    sys.exit(1)
print(f'r10 loader: {len(d):,} B, clean-screen handoff + R9 set (irqtest, GIC, ladder) all present')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
# the boot volume is what the kernel execs /sbin/launchd off: stage0 must be in there
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r10-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r10-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
# hide any Mu-Silicium tree for the duration of the pack.
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r10
        HIDDEN=1
fi
# ... and put the raphael shim in place of the redwood one mkxnuimg reads from prebuilt/uefi/,
# restored on exit (on a CI clone the tree is throwaway anyway)
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r10 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

python3 xnuloader/mkpayload.py -o /tmp/r10-payload.bin \
        "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
        --cmdline "$CMDLINE" --usb
python3 mkxnuimg.py -p /tmp/r10-payload.bin -o "$OUT"

python3 - "$OUT" "$R" "$KERNEL" "$ROOTAPFS" "$LOADER" "$CMDLINE" <<'PY'
import gzip, hashlib, struct, sys
img_path, R, kernel_path, rootapfs_path, loader_path, cmdline = sys.argv[1:7]
FD_SIZE = 0x300000
DTB_ADDR = 0x1F00000
def fail(m):
    print('FAIL: ' + m); sys.exit(1)

img = open(img_path, 'rb').read()
if img[:8] != b'ANDROID!':
    fail('not an Android boot image')
k_size = struct.unpack_from('<I', img, 8)[0]
r_size = struct.unpack_from('<I', img, 16)[0]
page   = struct.unpack_from('<I', img, 36)[0]
hdr    = struct.unpack_from('<I', img, 40)[0]
dtb_size = struct.unpack_from('<I', img, 1648)[0]
dtb_addr = struct.unpack_from('<Q', img, 1652)[0]
if (page, hdr, dtb_addr) != (4096, 2, DTB_ADDR):
    fail(f'v2 law broken: page={page} hdr={hdr} dtb_addr={dtb_addr:#x}')
k_off = page
r_off = (k_off + k_size + page - 1) // page * page
d_off = (r_off + r_size + page - 1) // page * page
if len(img) != d_off + dtb_size:
    fail(f'truncation law broken: {len(img):,} != {d_off + dtb_size:,}')
if img[d_off:d_off+4] != b'\xd0\x0d\xfe\xed':
    fail('DTB segment magic')

blob = gzip.decompress(img[k_off:k_off+k_size])
shim, fd, payload = blob[:176], blob[176:176+FD_SIZE], blob[176+FD_SIZE:]
ref = {n: open(R + '/' + n, 'rb').read() for n in
       ('BootShim.bin', 'SILICIUM_UEFI-raphael.fd', 'raphael.dtb')}
loader = open(loader_path, 'rb').read()
if shim != ref['BootShim.bin']:
    fail('shim differs from prebuilt/raphael/BootShim.bin')
if fd != ref['SILICIUM_UEFI-raphael.fd']:
    fail('FD differs from prebuilt/raphael/SILICIUM_UEFI-raphael.fd')
if img[d_off:d_off+dtb_size] != ref['raphael.dtb']:
    fail('DTB differs from prebuilt/raphael/raphael.dtb')
if blob[176+FD_SIZE:176+FD_SIZE+4] != b'XNUP':
    fail('XNUP does not sit exactly at shim+FD_SIZE')

# XNUP walk + identity
if payload[:4] != b'XNUP':
    fail('payload magic')
size = struct.unpack_from('<I', payload, 4)[0]
ents, p = {}, 8
while p + 24 <= 8 + size and payload[p] != 0:
    name = payload[p:p+16].rstrip(b'\0').decode()
    esz = struct.unpack_from('<I', payload, p+16)[0]
    ents[name] = payload[p+24:p+24+esz]
    p += 24 + ((esz + 15) & ~15)
if set(ents) != {'loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb'}:
    fail(f'payload entries: {sorted(ents)}')
if ents['loader.efi'] != loader:
    fail('loader.efi identity broken (not the one this build just compiled)')
for feat in (b'oem irqtest', b'DELIVERS as a Group 1 NS IRQ', b'R10 branch',
             b'R10 clean screen round', b'screen cleared for the handoff'):
    if feat not in ents['loader.efi']:
        fail(f'packed loader lacks the marker {feat!r}')
if b'debug=0x8' in ents['loader.efi']:
    fail('packed loader still carries debug=0x8')
if ents['kernel'] != open(kernel_path, 'rb').read():
    fail('kernel identity broken (not the patched r9 kernel this build just made)')
# the doorbell patch itself, verified inside the packed image (whole file: the sites sit at
# file offset ~0x470xxx, far beyond any small window)
k = ents['kernel']
if b'\x9f\x4e\x00\x71' not in k:                 # cmp w20, #0x13  (S4, little endian)
    fail('packed kernel does not carry the S4 dispatch patch')
if b'\x08\x01\xa0\x52' not in k:                 # movz w8, #0x8, lsl #16 = 0x80000 (S2)
    fail('packed kernel does not carry the S2 doorbell patch')
if ents['ramdisk'] != open(rootapfs_path, 'rb').read():
    fail('ramdisk identity broken')
if ents['cmdline'].rstrip(b'\0').decode() != cmdline:
    fail(f'cmdline identity broken: {ents["cmdline"]!r}')
if '-v' in ents['cmdline'].rstrip(b'\0').decode().split():
    fail('the packed cmdline still carries -v')
if len(payload) > (64 << 20) - 0x10000:
    fail('payload too big for the loader window')
print(f'r10 image verified: {len(img):,} B | payload {len(payload):,} B | DTB {dtb_size:,} B')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R10: clean cmdline prefix + black handoff)')
print(f'  kernel sha {hashlib.sha256(k).hexdigest()[:16]} (R9-patched: doorbell 27 -> 19, unchanged)')
print(f'  shim {hashlib.sha256(shim).hexdigest()[:16]} '
      f'FD {hashlib.sha256(fd).hexdigest()[:16]} '
      f'DTB {hashlib.sha256(ref["raphael.dtb"]).hexdigest()[:16]}')
print(f'  cmdline "{cmdline}" + usb (loader adds only cpus=1 rd=md0 ufs_shared_pa=0x<addr>; R10: no debug bits, no -v)')
PY
echo
echo "boot image: $OUT ($(stat -c %s "$OUT") bytes)"
echo "boot volume to upload as the resident image: $FLASHBOOT"
echo "phone side:"
echo "  fastboot boot  $OUT"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage $(basename "$FLASHBOOT")   # publish it beside the boot image first"
echo "  fastboot oem  run                             # black for a while, then the desktop"
echo "if the desktop never comes (loader says hung after ~60 s), before pulling the plug:"
echo "  fastboot oem log && fastboot get_staged r10-klog.txt   # the kernel's ring still fills"
echo "  fastboot oem irqtest && fastboot oem gic                # delivery verdict + the kernel's frame"
echo "  fastboot oem sgi 1 && fastboot oem gic                  # taken = alive; pends = still masked"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
echo "  (each on a fresh boot; the first N that never returns brackets the freeze)"
echo "for a verbose boot (console on screen), flash R9's image instead: raphael-xnu-r9-flash.img"
