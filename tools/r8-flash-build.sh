#!/bin/bash
# tools/r8-flash-build.sh -- pack the r8 SpringBoard boot image for raphael (K20 Pro, hackios).
#
# Same chain as r7 (r1 firmware / r2 shim / golden DTB / prebuilt kernel, dual-disk flash
# boot, same cmdline).  What r8 changes is the LOADER, rebuilt from xnuloader/:
# R7's dump closed the case -- the kernel's clock interrupt enabled and pending, never
# taken, the frame saying group 0 -- and the disassembly of the SHIPPED kernel closed the
# "who forgot the group write" question: gic_v3_irq_cpu_init() DOES write
# GICR_IGROUPR0 |= (1<<27), four instructions before the GICR_ISENABLER0 write that
# plainly landed (the dump sees exactly its 0x08000000).  A Non-secure write that does
# not take effect while its neighbours do means the GIC runs secure (DS=0) and the group
# registers are secure-only: writes dropped, reads blind.  The R7 "group 0" line was
# reading with its eyes shut, and no EL1 software -- kernel or loader -- can set the
# group on this chain the day the GIC enforces that.  So r8 is the live test and, in
# the world where the write sticks, the fix too:
#
#   gic_group_repair(): puts the kernel core's whole SGI+PPI bank in Group 1
#              (IGROUPR0=0xffffffff, IGRPMODR0=0), settles, and believes the readback.
#              Called at three moments: before CPU_ON (pre-arm, so a frame that keeps
#              state through the power-up hands the kernel a Group 1 clock from its
#              first instruction), once at the hung verdict (in case the pre-arm was
#              lost in the power-up or the kernel's own write was dropped), and by
#              oem gicgroup on demand.
#   oem gicgroup   the experiment: repair + readback + verdict.  Readback 0xffffffff
#              -> the write sticks: THIS world needs nothing else, a kernel wedged on
#              its clock comes back to life on the spot (watch the heartbeat, then
#              oem sgi 1).  Readback 0 -> the GIC drops Non-secure group writes: no EL1
#              software can have this interrupt as an IRQ on this chain, it pends as a
#              Group 0 FIQ this kernel never takes, and the fix is the monitor's or the
#              FIQ route (R9).  See docs/FLASH-R8.md for the decision tree.
#   oem gic / sgi / gicset / peek: as R7 (verdict lines updated with the R8 caveat).
#
# Packed from prebuilt/raphael/ (FD/shim/DTB unchanged since r1/r2) + prebuilt kernel:
#   BootShim.bin / SILICIUM_UEFI-raphael.fd / raphael.dtb   r1/r2-proven, byte-identical law
#   kernel                    prebuilt/mach.development.bcm2837 (the r2/r4-proven kernel)
#   loader.efi                BUILT HERE (R8): clang + lld-link, xnuloader/build.sh
#
# usage: tools/r8-flash-build.sh [tree]        (default: this repository)
#   ROOTAPFS=... FLASHBOOT=... OUT=...         overrides
#
# Phone side (Termux, after fastboot flash userdata + reboot to bootloader):
#   fastboot boot  raphael-xnu-r8-flash.img    # loader in RAM; its own USB fastboot comes up
#   fastboot oem  ramdisk                      # arm: the next download goes to the resident area
#   fastboot stage raphael-r8-flashboot.apfs   # the boot volume -> resident area = md0 (the root)
#   fastboot oem  run                          # kernel: md0=boot volume, md1=/redwood, then
#                                               # init's flash_root -> iOS volume on /dev/ufs_userdata
set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=$TREE/prebuilt/mach.development.bcm2837
OUT=${OUT:-$TREE/userland/raphael-xnu-r8-flash.img}
CMDLINE='-v ufs=2 bootcpu=7 root_apfs=ufs_userdata'

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r8-flash-build: missing $f"; exit 1; }
done

# The loader is the one piece this build owns: compile it (clang + lld-link must be in PATH).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r8-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
python3 - "$LOADER" <<'PY'
import sys
d = open(sys.argv[1], 'rb').read()
for feat in (b'oem gic', b'oem sgi', b'oem peek', b'NXSB at +32',
             b'as the firmware left it', b'never showed up as pending', b'oem gicset',
             b'oem gicgroup', b'group repair', b'pre-arm before CPU_ON',
             b'at the hung verdict'):
    if feat not in d:
        print(f'r8-flash-build: loader lacks the R8 marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
print(f'r8 loader: {len(d):,} B, R8 features (group repair, oem gicgroup, pre-arm) all present')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
# the boot volume is what the kernel execs /sbin/launchd off: stage0 must be in there
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r8-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r8-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
# hide any Mu-Silicium tree for the duration of the pack.
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r8
        HIDDEN=1
fi
# ... and put the raphael shim in place of the redwood one mkxnuimg reads from prebuilt/uefi/,
# restored on exit (on a CI clone the tree is throwaway anyway)
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r8 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

python3 xnuloader/mkpayload.py -o /tmp/r8-payload.bin \
        "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
        --cmdline "$CMDLINE" --usb
python3 mkxnuimg.py -p /tmp/r8-payload.bin -o "$OUT"

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
for feat in (b'oem gic', b'oem sgi', b'oem peek', b'NXSB at +32',
             b'as the firmware left it', b'never showed up as pending', b'oem gicset',
             b'oem gicgroup', b'group repair', b'pre-arm before CPU_ON',
             b'at the hung verdict'):
    if feat not in ents['loader.efi']:
        fail(f'packed loader lacks the R8 marker {feat!r}')
if ents['kernel'] != open(kernel_path, 'rb').read():
    fail('kernel identity broken')
if ents['ramdisk'] != open(rootapfs_path, 'rb').read():
    fail('ramdisk identity broken')
if ents['cmdline'].rstrip(b'\0').decode() != cmdline:
    fail(f'cmdline identity broken: {ents["cmdline"]!r}')
if len(payload) > (64 << 20) - 0x10000:
    fail('payload too big for the loader window')
print(f'r8 image verified: {len(img):,} B | payload {len(payload):,} B | DTB {dtb_size:,} B')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R8, built from xnuloader/)')
print(f'  shim {hashlib.sha256(shim).hexdigest()[:16]} '
      f'FD {hashlib.sha256(fd).hexdigest()[:16]} '
      f'DTB {hashlib.sha256(ref["raphael.dtb"]).hexdigest()[:16]} '
      f'kernel {hashlib.sha256(open(kernel_path,"rb").read()).hexdigest()[:16]}')
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
echo "if it hangs again (loader says hung after ~60 s; the repair runs once by itself), before pulling the plug:"
echo "  fastboot oem gicgroup                          # THE R8 EXPERIMENT: repair + readback + verdict"
echo "  fastboot oem sgi 1 && fastboot oem gic          # then: taken = back alive; pends = still blind"
echo "  fastboot oem log && fastboot get_staged r8-klog.txt   # the whole story is in there"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
echo "  (each on a fresh boot; the first N that never returns brackets the freeze)"
echo "  fastboot oem gicset && fastboot oem run         # as R7: pre-set G1NS|ARE"
echo "  fastboot oem screenshot && fastboot get_staged screen.raw && fastboot getvar screen"
