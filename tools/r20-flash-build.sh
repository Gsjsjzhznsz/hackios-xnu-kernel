#!/bin/bash
# tools/r20-flash-build.sh -- pack the r20 SpringBoard boot images for raphael (K20 Pro, hackios).
#
# R20 = the form written is the phone's own at last. The three-round mystery
#       ("the hypervisor drops everything for the gfx streams") solved from
#       this phone's own device tree, not sc7280's:
#
#   KERNEL (xnu/files/bsd/dev/raphael_adreno.c):
#     - The port carried sc7280's mask over unchanged -- one 0x400 for every
#       "iommus" of the SMMU -- and R17, R18 and R19 wrote it for the gfx
#       streams, three ways (entry scan, bank first, entry 0 plain), and the
#       SMMU took none of it: the readback was 0 every time.
#     - raphael's own dt (the 499823-byte vendor dtb this project carries;
#       parsed twice, once per session, byte for byte) says:
#         kgsl-iommu@2CA0000/gfx3d_user    (sid 0, mask 0x401)  <- one entry for streams 0 and 1
#         kgsl-iommu@2CA0000/gfx3d_secure  (sid 2, mask 0x400)
#         gmu@2C6A000/gmu_user             (sid 4, mask 0x400)
#         gmu@2C6A000/gmu_kernel           (sid 5, mask 0x400)  <- what the driver always wrote:
#                                                                  it landed every time, because
#                                                                  raphael's gmu form is, by
#                                                                  coincidence, sc7280's
#       The GMU was never stopped by the hypervisor at all -- it was writing
#       its own phone's form by luck. The gfx streams were writing a form
#       this phone's SMMU has never been asked to take. Same mechanism, two
#       different trees, both directions witnessed (sc7280: mask 0x401 reads
#       back 0; raphael: mask 0x400 for the gfx streams reads back 0).
#     - R20 changes the value, not the order: the bank-first order R19
#       restored stays; what goes into the entry is raphael's own form --
#       smr(entry 0) = VALID | 0x401<<16 | stream 0 = 0x84010000.
#     - stream 1 (gfx3d_lpac): raphael's dt has NO separate entry for it --
#       the 0x401 mask on entry 0 covers streams 0 and 1, both through
#       context 0. R19's unconditional entry 2 ((sid 1, mask 0x400), the
#       sc7280 form) is gone by default; adreno_stream1=1/2 is demoted to a
#       forced experiment that writes it anyway, for comparison.
#     - space_stream()/space_on() take the mask as an argument now; the GMU
#       path passes SMMU_MASK_GMU (0x400 -- behavior unchanged, its writes
#       have always landed), the core path passes SMMU_MASK_GFX (0x401).
#   PACKAGING: unchanged from R17 (R13 volume bytes behind the eight content gates).
#
# Two images, one release:
#   raphael-xnu-r20-flash.img       touchscreen=1 buttons=1 cpus=8 -- the daily eight-core one
#   raphael-xnu-r20-gpu-flash.img   the same, and gpu=1 -- the ladder, into step 18's water
#
# usage: tools/r20-flash-build.sh [tree]        (default: this repository)
#   KERNEL=... ROOTAPFS=... FLASHBOOT=... OUT=... OUTGPU=...   overrides

set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}
OUT=${OUT:-$TREE/userland/raphael-xnu-r20-flash.img}
OUTGPU=${OUTGPU:-$TREE/userland/raphael-xnu-r20-gpu-flash.img}
CMDLINE='touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata'
CMDLINE_GPU="$CMDLINE gpu=1"

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r20-flash-build: missing $f (kernel: built by xnu-build.sh -- see the workflow or xnu/README.md)"; exit 1; }
done
echo "r20 kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"

# The loader is the one this tree compiles (R10's, unchanged): compile it (clang + lld-link).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r20-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
python3 - "$LOADER" <<'PY'
import sys
d = open(sys.argv[1], 'rb').read()
for feat in (b'oem gic', b'oem sgi', b'oem peek', b'NXSB at +32',
             b'as the firmware left it', b'never showed up as pending', b'oem gicset',
             b'oem gicgroup', b'group repair', b'pre-arm before CPU_ON',
             b'at the hung verdict',
             b'oem irqtest', b'DELIVERS as a Group 1 NS IRQ', b'R10 branch',
             b'the readback reads 0',
             b'R10 clean screen round', b'screen cleared for the handoff',
             # R14: the loader's half of the cores (written once cpus= is in the payload's
             # own arguments -- the string is in the binary either way; the run decides)
             b'cores for the kernel, in the order it takes them'):
    if feat not in d:
        print(f'r20-flash-build: loader lacks the marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
if b'debug=0x8' in d:
    print('r20-flash-build: loader still carries debug=0x8')
    sys.exit(1)
print(f'r20 loader: {len(d):,} B (R10 0.10, unchanged)')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r20-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r20-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r20
        HIDDEN=1
fi
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r20 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

pack_one() {
        local name="$1" out="$2" cmdline="$3"
        python3 xnuloader/mkpayload.py -o /tmp/r20-payload.bin \
                "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
                --cmdline "$cmdline" --usb
        python3 mkxnuimg.py -p /tmp/r20-payload.bin -o "$out"
        python3 - "$out" "$R" "$KERNEL" "$ROOTAPFS" "$LOADER" "$cmdline" <<'PY'
import gzip, hashlib, struct, sys
img_path, R, kernel_path, rootapfs_path, loader_path, cmdline = sys.argv[1:7]
FD_SIZE = 0x300000
DTB_ADDR = 0x1F00000
def fail(m):
        print('FAIL [' + img_path + ']: ' + m); sys.exit(1)

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
for feat in (b'oem irqtest', b'R10 clean screen round', b'screen cleared for the handoff',
             b'cores for the kernel, in the order it takes them'):
        if feat not in ents['loader.efi']:
                fail(f'packed loader lacks the marker {feat!r}')
if b'debug=0x8' in ents['loader.efi']:
        fail('packed loader still carries debug=0x8')
if ents['kernel'] != open(kernel_path, 'rb').read():
        fail('kernel identity broken (not the source-built r20 kernel)')
# the doorbell, verified in the SOURCE-BUILT kernel: the macro fix must have compiled in
k = ents['kernel']
if b'\x9f\x4e\x00\x71' not in k:                 # cmp w20, #0x13  (the dispatch: INTID==19)
        fail('the built kernel does not dispatch INTID 19 to the clock -- GIC_INTID_VTIMER unchanged?')
if b'\x08\x01\xa0\x52' not in k:                 # movz w8, #0x8, lsl #16 = 1<<19 (the enable)
        fail('the built kernel does not enable INTID 19 -- GIC_INTID_VTIMER unchanged?')
# the rounds' own markers
for feat in (b'RAPHAEL TOUCH', b'GT9886', b'buttons: step 1', b'PMIC arbiter',
             b'a640_gmu.bin', b'a640_zap.mbn', b'the panel dimmed to',
             b'going on with the configured rates',
             b'going with the ID registers',
             b"this firmware's verdict of started",
             b"the power-on block's pull-ups",
             b"'s status byte changed to",
             b"the two pages the GMU's own code asks for",
             b"cb tlbiall",
             b"a context bank nobody used before",
             b"trying context bank",
             b"tlbiallnsnh",
             b"the stream the bank names for it",
             b"what our own tables say for",
             b"arrived late",
             b"make the SMMU forget every translation again",
             b"survivable, cleared, the wait goes on",
             b"while the GMU was deciding",
             b"has stopped answering",
             b"with a bus error",
             b"the patience was not wasted",
             b"while the 3D core was powered",
             # R19 kept: the order stays, and so does the =1 experiment's wording
             b"the bank is programmed and ON before the stream table is touched",
             b"the core's second stream (1) through the same context",
             # R20: the phone's own form
             b"raphael's dt gives the gfx streams the mask 0x401",
             b"the second stream (1) under entry 0's own wing"):
        if feat not in k:
                fail(f'the built kernel lacks the marker {feat!r}')
# the R18 markers, and R19's verdict, must be GONE: the scanning and the verdict
# they lived in are deleted/retired this round
for gone in (b'of the stream table does not keep what is written',
             b'of the stream table instead',
             b'the free entries are offered one by one',
             b'refused every entry',
             b'the hypervisor drops SMR/S2CR'):
        if gone in k:
                fail(f'the built kernel still carries a retired marker {gone!r} -- the round that deleted it was not applied')
if ents['ramdisk'] != open(rootapfs_path, 'rb').read():
        fail('ramdisk identity broken')
if ents['cmdline'].rstrip(b'\0').decode() != cmdline:
        fail(f'cmdline identity broken: {ents["cmdline"]!r}')
packed = ents['cmdline'].rstrip(b'\0').decode()
for needle in ('touchscreen=1', 'buttons=1', 'cpus=8'):
        if needle not in packed.split():
                fail(f'the packed cmdline lacks the token {needle}')
if 'cpus=1' in packed.split():
        fail('the packed cmdline still holds the kernel to one core')
if '-v' in packed.split():
        fail('the packed cmdline still carries -v')
if len(payload) > (64 << 20) - 0x10000:
        fail('payload too big for the loader window')
print(f'r20 image verified: {img_path}: {len(img):,} B | payload {len(payload):,} B | cmdline "{cmdline}"')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R10 0.10, unchanged) '
      f'kernel sha {hashlib.sha256(k).hexdigest()[:16]} (SOURCE-BUILT r20)')
print(f'  shim {hashlib.sha256(shim).hexdigest()[:16]} '
      f'FD {hashlib.sha256(fd).hexdigest()[:16]} '
      f'DTB {hashlib.sha256(ref["raphael.dtb"]).hexdigest()[:16]}')
PY
}

pack_one main "$OUT" "$CMDLINE"
pack_one gpu  "$OUTGPU" "$CMDLINE_GPU"

echo
echo "boot images:"
echo "  $OUT ($(stat -c %s "$OUT") bytes) -- touchscreen=1 buttons=1 cpus=8"
echo "  $OUTGPU ($(stat -c %s "$OUTGPU") bytes) -- + gpu=1 (the ladder, into step 18)"
echo "boot volume to upload as the resident image: $FLASHBOOT"
echo "phone side (primary image):"
echo "  fastboot boot  $OUT"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage $(basename "$FLASHBOOT")   # ONLY this file -- see the warning below"
echo "  fastboot oem  run          # desktop in ~1 min; touch and keys live; cores come up on their own"
echo "phone side (GPU experiment, second boot):"
echo "  the same four steps with $OUTGPU"
echo ""
echo "  ⚠️  THE RESIDENT IMAGE (md0, the root) IS $(basename "$FLASHBOOT") -- nothing else."
echo "  ⚠️  R14's accident: the md1 backup staged instead; md1 mounts on /redwood with no"
echo "      launchd jobs and no libredwood.dylib -> the '[FAIL] create the directories of the"
echo "      data volume' line and a black screen.  The small root travels INSIDE the boot images"
echo "      as the payload ramdisk; it is not something to stage."
echo ""
echo "what to read back (the R20 ladder):"
echo "  steps 1-17 are R17's again ('the GMU accepts' x4, 'THE 3D CORE HAS POWER"
echo "  AND ANSWERS') -- nothing before step 18 changed by one byte of behavior."
echo "  step 18 is the round:"
echo "    . the announce now says it: '... the bank is programmed and ON before the"
echo "      stream table is touched, and what the entry takes is the phone's own form"
echo "      -- raphael's dt gives the gfx streams the mask 0x401 (one entry for streams"
echo "      0 and 1); sc7280's 0x400, which the port carried over, is what R17, R18 and"
echo "      R19 kept writing, and the SMMU kept none of'"
echo "    . THE VICTORY LINE, the first SMR landing for a gfx stream since R17:"
echo "        about to write smmu smr at 0x2ca0800: 0x84010000     <- VALID | 0x401<<16 | stream 0"
echo "        smmu smr (0x2ca0800) = 0x84010000                     <- it read back. R20 is won."
echo "      (R17-R19 wrote 0x84000000 there -- sc7280's form, 0x400 -- and it read back 0.)"
echo "    . then the default path prints 'the second stream (1) under entry 0's own wing:"
echo "      raphael's dt has no separate entry for it...' -- no entry 2 write at all"
echo "    . step 18 done -> 19 (zap / TZ PAS 13), 20 (CP start), 21 (commands): new water."
echo "  if entry 0 STILL reads 0 with the phone's own form written:"
echo "      the klog is the evidence; R21's battlefield is then the TCU/TBUs"
echo "      (gfx_0_tbu@0x2cc5000 status@0x2cc2200, gfx_1_tbu@0x2cc9000 status@0x2cc2208),"
echo "      the global side (sCR0.USFCFG), or the loader timeline -- in that order."
echo "  adreno_stream1=1 / 2 (cmdline) forces the old experiment for comparison:"
echo "      1 = entry 2 through context 0 (sc7280's form); 2 = an empty context of its own."
echo "  if a boot dies early, before pulling the plug:"
echo "  fastboot oem log && fastboot get_staged r20-klog.txt   # every step's line is in there"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
