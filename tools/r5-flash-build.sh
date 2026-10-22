#!/bin/bash
# tools/r5-flash-build.sh -- pack the r5 SpringBoard boot image for raphael (K20 Pro, pure hackios).
#
# Runs in a hackios-xnu-kernel tree after userland/build.sh (PREBUILT=1 FLASHBOOT=1 CRYPTEXES=flash)
# has made BOTH images.  The flash boot is TWO disks (upstream's own design -- stage0.c hard-requires
# /dev/md1, and the kernel execs /sbin/launchd off md0, so a single-ramdisk payload cannot boot off
# the flash):
#
#   md0 (the root, rd=md0)   userland/flashboot.apfs -- the boot volume: stage0 as /sbin/launchd,
#                            mount points (Cryptexes/OS, Cryptexes/App, ...).  Uploaded at boot
#                            time as the loader's RESIDENT image (fastboot oem ramdisk + stage).
#   md1 (/redwood)           userland/root.apfs -- the small image: init, LaunchDaemons, tests.
#                            Travels IN the payload as its ramdisk.
#
# Packed with the raphael boot chain from prebuilt/raphael/:
#
#   loader.efi                  xnuloader with the ufs=2 block service + platform_raphael
#                               (the r4-hardware-proven build, sha256 9800946a...)
#   BootShim.bin                the raphael shim: FD at 0x9FC00000 +0x300000, XNUP -> 0xF0000000
#                               (r2-hardware-proven, sha256 310db2f5...)
#   SILICIUM_UEFI-raphael.fd    the 3 MB firmware built from the phone's own XBL (r1-hardware-proven,
#                               sha256 0c47bdeb...; PartitionDxe + UFSDxe inside)
#   raphael.dtb                 the golden raphael DTB (sha256 2a1ec87b...)
#   kernel                      prebuilt/mach.development.bcm2837 (the r2/r4-proven hackios kernel)
#   ramdisk                     userland/root.apfs (becomes md1, /redwood, once the resident
#                               image is up -- stage0 mounts it and runs its init)
#   usb                         the loader stays resident and takes fastboot commands over its
#                               own USB link: the phone-side sequence below is how the boot goes
#   cmdline                     -v ufs=2 bootcpu=7 root_apfs=ufs_userdata
#
# The packed image is verified afterwards (v2 header law, proven shim/FD/DTB/loader bytes,
# XNUP walk, cmdline identity) -- the same checks build_r4.py ran for r4.
#
# usage: tools/r5-flash-build.sh [tree]        (default: this repository)
#   ROOTAPFS=... FLASHBOOT=... OUT=...         overrides
#
# Phone side (Termux, after fastboot flash userdata + reboot to bootloader):
#   fastboot boot  raphael-xnu-r5-flash.img    # loader in RAM; it brings up its own USB fastboot
#   fastboot oem  ramdisk                      # arm: the next download goes to the resident area
#   fastboot stage raphael-r5-flashboot.apfs   # the boot volume -> resident area = md0 (the root)
#   fastboot oem  run                          # kernel: md0=boot volume, md1=/redwood, then
#                                               # init's flash_root -> iOS volume on /dev/ufs_userdata
set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=$TREE/prebuilt/mach.development.bcm2837
OUT=${OUT:-$TREE/userland/raphael-xnu-r5-flash.img}
CMDLINE='-v ufs=2 bootcpu=7 root_apfs=ufs_userdata'

for f in "$R/loader.efi" "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/mkpayload.py mkxnuimg.py \
         prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r5-flash-build: missing $f"; exit 1; }
done
M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
# the boot volume is what the kernel execs /sbin/launchd off: stage0 must be in there
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r5-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r5-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
# hide any Mu-Silicium tree for the duration of the pack.
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r5
        HIDDEN=1
fi
# ... and put the raphael shim in place of the redwood one mkxnuimg reads from prebuilt/uefi/,
# restored on exit (on a CI clone the tree is throwaway anyway)
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r5 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

python3 xnuloader/mkpayload.py -o /tmp/r5-payload.bin \
        "loader.efi=$R/loader.efi" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
        --cmdline "$CMDLINE" --usb
python3 mkxnuimg.py -p /tmp/r5-payload.bin -o "$OUT"

python3 - "$OUT" "$R" "$KERNEL" "$ROOTAPFS" "$CMDLINE" <<'PY'
import gzip, hashlib, struct, sys
img_path, R, kernel_path, rootapfs_path, cmdline = sys.argv[1:6]
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
       ('loader.efi', 'BootShim.bin', 'SILICIUM_UEFI-raphael.fd', 'raphael.dtb')}
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
if ents['loader.efi'] != ref['loader.efi']:
    fail('loader.efi identity broken')
if ents['kernel'] != open(kernel_path, 'rb').read():
    fail('kernel identity broken')
if ents['ramdisk'] != open(rootapfs_path, 'rb').read():
    fail('ramdisk identity broken')
if ents['cmdline'].rstrip(b'\0').decode() != cmdline:
    fail(f'cmdline identity broken: {ents["cmdline"]!r}')
if len(payload) > (64 << 20) - 0x10000:
    fail('payload too big for the loader window')
print(f'r5 image verified: {len(img):,} B | payload {len(payload):,} B | DTB {dtb_size:,} B')
print(f'  loader sha {hashlib.sha256(ref["loader.efi"]).hexdigest()[:16]} '
      f'shim {hashlib.sha256(shim).hexdigest()[:16]} '
      f'FD {hashlib.sha256(fd).hexdigest()[:16]} '
      f'DTB {hashlib.sha256(ref["raphael.dtb"]).hexdigest()[:16]}')
print(f'  cmdline "{cmdline}" + usb (loader prepends debug=0x8 cpus=1 rd=md0 ufs_shared_pa=0x<addr>)')
PY
echo
echo "boot image: $OUT ($(stat -c %s "$OUT") bytes)"
echo "boot volume to upload as the resident image: $FLASHBOOT"
echo "phone side:"
echo "  fastboot boot  $OUT"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage $(basename "$FLASHBOOT")   # publish it beside the boot image first"
echo "  fastboot oem  run"
echo "after it works, one addition at a time (see FLASH-R4-SPRINGBOARD.md section 6):"
echo "  cpus=8  touchscreen=1  (gpu=1 needs fw-gpu with raphael's a640 firmware first)"
