#!/bin/bash
# tools/r9-flash-build.sh -- pack the r9 SpringBoard boot image for raphael (K20 Pro, hackios).
#
# R9 = the doorbell round.  The R8 verdict "IGROUPR0 0 -> 0: the write did not stick" is
# CORRECTED by R9's research: that register READS blind (RAZ) from Non-secure when the
# GIC runs secure -- a 0 readback cannot tell a dropped write from a landed one, and
# Linux mainline writes that very register from Non-secure EL1 on every boot
# (irq-gic-v3.c:1293, "Configure SGIs/PPIs as non-secure Group-1") with raphael-Linux
# running daily on this SoC.  What R7/R8 actually caught, hiding in the pending line
# all along:
#
#   enabled 8000000        the kernel enabled INTID 27 -- the QEMU/bcm2837 vtimer
#   pending 10080000       the tick pends on bit 28 + bit 19 -- bit 19 = INTID 19 =
#                          the EL1 virtual timer on SM8150 (mainline sm8150.dtsi:
#                          timer { <GIC_PPI 3 ...> /* virt */ } = INTID 16+3)
#
# The kernel rings doorbell 27; the silicon wired the bell at 19.  The group was never
# the story.  R9 changes:
#
#   KERNEL: tools/r9-kernel-patch.py retargets the shipped prebuilt (byte-anchored,
#           capstone-verified on both ends, exactly 4 words / 6 bytes):
#             _gic_v3_irq_cpu_init: movk w8,#0x800,lsl#16 -> #0x008   (>=2-cpu path)
#                                    mov  w8,#0x8000000    -> #0x80000 (this chain: cpus=1)
#                                    strb w10,[x9,#0x39b]  -> #0x393   (IPRIORITYR(19))
#             _gic_irq_handler:     cmp  w20,#0x1b        -> #0x13    (dispatch: INTID==vtimer)
#           The IGROUPR0/IGRPMODR0/ISENABLER0 writes in the same function use the same
#           w8 bit and follow for free; CNTV arming / EOI / SGI code is INTID-independent.
#   LOADER 0.9: oem irqtest -- the delivery experiment on the loader's OWN core (see
#           below); GIC_VTIMER_INTID 27 -> 19; verdict lines carry the R9 correction.
#           gic_group_repair stays as belt-and-braces.
#
# oem irqtest (before oem run, or while a kernel hangs elsewhere): arms the loader's
# own CNTV ~50 ms out, enables INTID 19 in its own bank, sets ICC_PMR=0xff +
# ICC_IGRPEN1=1 (the pair the kernel's bring-up writes), polls ISPENDR0 until bit 19
# pends, then reads ICC_IAR1_EL1 ONCE -- an ack works even with PSTATE.I set:
#   IAR1 answers 19        -> the tick DELIVERS as a Group 1 NS IRQ: the kernel patch
#                             is sufficient; run the kernel and watch the heartbeat
#   IAR1 answers spurious  -> the bank will not signal it as an IRQ (the group story
#                             is real): R10 = EL3 monitor or the kernel taking FIQ
#   nothing ever pends     -> the vtimer is wired elsewhere after all: the pending
#                             line says where
#
# Packed from prebuilt/raphael/ (FD/shim/DTB unchanged since r1/r2) + the PATCHED
# prebuilt kernel; loader.efi BUILT HERE (R9) from xnuloader/.
#
# usage: tools/r9-flash-build.sh [tree]        (default: this repository)
#   ROOTAPFS=... FLASHBOOT=... OUT=...         overrides
#
# Phone side (Termux, after fastboot flash userdata + reboot to bootloader):
#   fastboot boot  raphael-xnu-r9-flash.img
#   fastboot oem  irqtest                     # optional: the delivery experiment, no kernel needed
#   fastboot oem  ramdisk
#   fastboot stage raphael-r9-flashboot.apfs
#   fastboot oem  run                          # heartbeat 1 -> 2 -> 3 ... = the kernel is alive
set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL_SRC=$TREE/prebuilt/mach.development.bcm2837
KERNEL=$TREE/prebuilt/mach.development.bcm2837.r9
OUT=${OUT:-$TREE/userland/raphael-xnu-r9-flash.img}
CMDLINE='-v ufs=2 bootcpu=7 root_apfs=ufs_userdata'

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL_SRC" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r9-flash-build: missing $f"; exit 1; }
done

# The one kernel change this build owns: retarget the vtimer doorbell 27 -> 19.
# The patcher byte-anchors every site, verifies semantics on both ends with capstone,
# and refuses to leave a half-patched file behind.
python3 -c "import capstone" 2>/dev/null \
        || python3 -m pip install --quiet --break-system-packages capstone \
        || python3 -m pip install --quiet --user --break-system-packages capstone
python3 tools/r9-kernel-patch.py "$KERNEL_SRC" "$KERNEL"

# The loader is the other piece this build owns: compile it (clang + lld-link must be in PATH).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r9-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
python3 - "$LOADER" <<'PY'
import sys
d = open(sys.argv[1], 'rb').read()
for feat in (b'oem gic', b'oem sgi', b'oem peek', b'NXSB at +32',
             b'as the firmware left it', b'never showed up as pending', b'oem gicset',
             b'oem gicgroup', b'group repair', b'pre-arm before CPU_ON',
             b'at the hung verdict',
             b'oem irqtest', b'DELIVERS as a Group 1 NS IRQ', b'R10 branch',
             b'the readback reads 0'):
    if feat not in d:
        print(f'r9-flash-build: loader lacks the R9 marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
print(f'r9 loader: {len(d):,} B, R9 features (oem irqtest, corrected verdicts) + R8 set all present')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
# the boot volume is what the kernel execs /sbin/launchd off: stage0 must be in there
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r9-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r9-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
# hide any Mu-Silicium tree for the duration of the pack.
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r9
        HIDDEN=1
fi
# ... and put the raphael shim in place of the redwood one mkxnuimg reads from prebuilt/uefi/,
# restored on exit (on a CI clone the tree is throwaway anyway)
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r9 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

python3 xnuloader/mkpayload.py -o /tmp/r9-payload.bin \
        "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
        --cmdline "$CMDLINE" --usb
python3 mkxnuimg.py -p /tmp/r9-payload.bin -o "$OUT"

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
for feat in (b'oem irqtest', b'DELIVERS as a Group 1 NS IRQ', b'R10 branch'):
    if feat not in ents['loader.efi']:
        fail(f'packed loader lacks the R9 marker {feat!r}')
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
if len(payload) > (64 << 20) - 0x10000:
    fail('payload too big for the loader window')
print(f'r9 image verified: {len(img):,} B | payload {len(payload):,} B | DTB {dtb_size:,} B')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R9, built from xnuloader/)')
print(f'  kernel sha {hashlib.sha256(k).hexdigest()[:16]} (R9-patched: doorbell 27 -> 19)')
print(f'  shim {hashlib.sha256(shim).hexdigest()[:16]} '
      f'FD {hashlib.sha256(fd).hexdigest()[:16]} '
      f'DTB {hashlib.sha256(ref["raphael.dtb"]).hexdigest()[:16]}')
print(f'  cmdline "{cmdline}" + usb (loader prepends debug=0x8 cpus=1 rd=md0 ufs_shared_pa=0x<addr>)')
PY
echo
echo "boot image: $OUT ($(stat -c %s "$OUT") bytes)"
echo "boot volume to upload as the resident image: $FLASHBOOT"
echo "phone side:"
echo "  fastboot boot  $OUT"
echo "  fastboot oem  irqtest                          # optional: delivery experiment (no kernel needed)"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage $(basename "$FLASHBOOT")   # publish it beside the boot image first"
echo "  fastboot oem  run                             # heartbeat 1 -> 2 -> 3 ... = alive"
echo "if the kernel still hangs (loader says hung after ~60 s), before pulling the plug:"
echo "  fastboot oem irqtest && fastboot oem gic        # delivery verdict + the kernel's frame"
echo "  fastboot oem sgi 1 && fastboot oem gic          # then: taken = back alive; pends = still masked"
echo "  fastboot oem log && fastboot get_staged r9-klog.txt   # the whole story is in there"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
echo "  (each on a fresh boot; the first N that never returns brackets the freeze)"
