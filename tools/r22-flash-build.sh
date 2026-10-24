#!/bin/bash
# tools/r22-flash-build.sh -- pack the r22 SpringBoard boot images for raphael (K20 Pro, hackios).
#
# R22 = the -3145797 refusal, bisected.  R21's battle report: the TrustZone channel is fully
#       alive (0xc2000601 probes answered), but PAS_INIT_IMAGE (0xc2000201) for the zap shader
#       (pas 13, metadata at 0xff014000) comes back -3145797 (0xFFCFFFBB), answer 0 -- the call
#       ANSWERED, the image NOT TAKEN.  Two things of ours differed from the two working forms
#       (redwood's, which produced the GPU on its phone, and Linux's qcom_scm.c):
#
#   1. THE FORM OF THE CALL.  R21 sent the PAS calls "at once" (SCM_FAST, like the probes).
#      Both working forms send them PREEMPTIBLE (redwood's step 12: "a call that may be
#      preempted, as Linux's is"; the SCM_INTERRUPTED + session retry dance in the wrapper
#      exists exactly for this).  A signature check does not fit in an "at once" call.
#   2. THE LAYOUT OF THE METADATA.  TrustZone reads the buffer through the ELF header IT
#      carries: it takes the hashes from the phdr's own p_offset (mdt_loader.c:
#      qcom_mdt_read_metadata).  a640_zap.mbn declares them at p_offset 0x1000; R21 packed
#      them at 0x94 (the .mdt's shape, where that IS the p_offset), so TrustZone looked at
#      0x1000 and found the middle of the packed block.  Linux's zap path passes the WHOLE
#      .mbn (qcom_scm_pas_init_image(13, fw->data, fw->size)); 14256 B fits in one 16 KB page.
#      Steps 2/4 back this read: the header pair is 0x94 B, the hashes 0x1a38 B at 0x1000.
#
#   KERNEL (xnu/files/bsd/dev/raphael_adreno.c):
#     - zap_header(phys, whole): builds BOTH metadata forms in a physically contiguous,
#       page-aligned 16 KB page, cache-cleaned: the whole file (Linux's), or R21's packed
#       shape (kept to isolate the variable).  Hashes always reported with their file offset.
#     - step 19 tries, in order:  (1) whole .mbn + preemptible call, (2) R21's packed
#       metadata + preemptible call, (3) whole .mbn + "at once" call.  Between the attempts
#       the give-up call (svc2/cmd6 -- the one R21 proved ANSWERED, returns 0) takes the
#       refusal back.  The winner's call form carries the rest of the chain (MEM_SETUP 2,2
#       with the image's one page 0x1000 -- redwood's working call says 0x1000 too -- then
#       AUTH_AND_RESET 2,5).  Each attempt prints its own refusal code.
#     - The wrapper now says how many times a call was preempted before it was answered.
#   PACKAGING: unchanged from R17 (R13 volume bytes behind the eight content gates).
#
# Two images, one release:
#   raphael-xnu-r22-flash.img       touchscreen=1 buttons=1 cpus=8 -- the daily eight-core one
#   raphael-xnu-r22-gpu-flash.img   the same, and gpu=1 -- the ladder, into step 19's water
#
# usage: tools/r21-flash-build.sh [tree]        (default: this repository)
#   KERNEL=... ROOTAPFS=... FLASHBOOT=... OUT=... OUTGPU=...   overrides
# usage: tools/r21-flash-build.sh [tree]        (default: this repository)
#   KERNEL=... ROOTAPFS=... FLASHBOOT=... OUT=... OUTGPU=...   overrides

set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}
OUT=${OUT:-$TREE/userland/raphael-xnu-r22-flash.img}
OUTGPU=${OUTGPU:-$TREE/userland/raphael-xnu-r22-gpu-flash.img}
CMDLINE='touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata'
CMDLINE_GPU="$CMDLINE gpu=1"

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r22-flash-build: missing $f (kernel: built by xnu-build.sh -- see the workflow or xnu/README.md)"; exit 1; }
done
echo "r22 kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"

# The loader is the one this tree compiles (R10's, unchanged): compile it (clang + lld-link).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r22-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
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
        print(f'r22-flash-build: loader lacks the marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
if b'debug=0x8' in d:
    print('r22-flash-build: loader still carries debug=0x8')
    sys.exit(1)
print(f'r22 loader: {len(d):,} B (R10 0.10, unchanged)')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r22-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r22-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r22
        HIDDEN=1
fi
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r22 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

pack_one() {
        local name="$1" out="$2" cmdline="$3"
        python3 xnuloader/mkpayload.py -o /tmp/r22-payload.bin \
                "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
                --cmdline "$cmdline" --usb
        python3 mkxnuimg.py -p /tmp/r22-payload.bin -o "$out"
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
        fail('kernel identity broken (not the source-built r21 kernel)')
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
             # R21: the phone's own form
             b"raphael's dt gives the gfx streams the mask 0x401",
             b"the second stream (1) under entry 0's own wing",
             # R21: TrustZone, asked this phone's own way (the give-up call is not
             # sent by default -- raphael's TrustZone took the processor with it;
             # the questions this phone's own Linux asks come first, the place is
             # this phone's pil_gpu_region, the .mbn's hashes are found at last)
             b"raphael's TrustZone took the processor with it",
             b"the give-up call is not sent by default",
             b"no zap shader can be asked for",
             b"the whole door is dark",
             b"adreno_tzunload",
             b"with the channel proven",
             b'whether it "supports" the GPU\'s image',
             # R22: the metadata and the form of the call (the -3145797 refusal, bisected)
             b"three attempts -- the whole .mbn as the metadata",
             b"attempt %u of 3",
             b"the whole .mbn as the metadata, a call that may be preempted (Linux's form)",
             b"R21's packed metadata, a call that may be preempted",
             b"the metadata is built as",
             b"the whole file, every p_offset at the real bytes (Linux's)",
             b"the call was preempted %d time%s before it was answered",
             b"the give-up call takes the refusal back",
             b"a one-page metadata the way it is asked",
             b"attempt %u refused: the call returns %d, its answer %#llx",
             b"the next attempt goes on all the same"):
        if feat not in k:
                fail(f'the built kernel lacks the marker {feat!r}')
# the R18 markers, and R19's verdict, must be GONE: the scanning and the verdict
# they lived in are deleted/retired this round
for gone in (b'of the stream table does not keep what is written',
             b'of the stream table instead',
             b'the free entries are offered one by one',
             b'refused every entry',
             b'the hypervisor drops SMR/S2CR',
             b'TAKEN TO MEAN THAT IT KEEPS A ZAP SHADER'):
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
print(f'r22 image verified: {img_path}: {len(img):,} B | payload {len(payload):,} B | cmdline "{cmdline}"')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R10 0.10, unchanged) '
      f'kernel sha {hashlib.sha256(k).hexdigest()[:16]} (SOURCE-BUILT r22)')
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
echo "what to read back (the R22 ladder -- the -3145797 refusal, bisected):"
echo "  steps 1-18 are R21's again (the channel probes answered, then attempt 1):"
echo "    . 'attempt 1 of 3: the whole .mbn as the metadata, a call that may be"
echo "       preempted (Linux's form)' -> 'TrustZone returns 0, answer 0' ="
echo "      THE SIGNATURE IS TAKEN: the metadata layout + call form was the wall."
echo "      Then: where the image will be (0xc2000202, 0x98f15000, one page) ->"
echo "      'about to write 1968 bytes at 0x98f15000' -> 0xc2000205 ->"
echo "      'TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER' = step 19"
echo "      done -> step 20 (CP out of secure mode) -> 21 (commands): NEW WATER."
echo "    . attempt 1 refused with -3145797 again?  attempt 2 (R21's packed"
echo "      metadata, same form) decides:"
echo "        - attempt 2 TAKEN  = the packed layout was fine, the form was the"
echo "          wall (and attempt 1's whole-file layout has some other fault:"
echo "          read the codes -- if they differ, the layout matters either way)"
echo "        - attempt 2 refused, SAME code = the layout is not the variable;"
echo "          attempt 3 (whole .mbn, 'at once' call) decides:"
echo "            - attempt 3 TAKEN = the preemptible form was fine, the form"
echo "              flip was the wall (R21's fast call was refused)"
echo "            - attempt 3 refused, SAME code = neither layout nor form:"
echo "              the code belongs to the memory or the state.  R23's"
echo "              battlefield: WHERE the metadata lives (TZ-visible memory),"
echo "              or the PAS state the probes cannot see (kgsl-hyp)."
echo "        - a DIFFERENT code on attempts 2/3 = progress: send the whole log."
echo "    . 'the call was preempted N times before it was answered' = the"
echo "      preemptible dance is LIVE (exactly what redwood's step 12 promised)."
echo "    . all refused -> the diagnostics (supports GPU vs audio, then the"
echo "      give-up call) close the step, as in R21; the system draws with the"
echo "      software renderer and the cleanup stays complete."
echo "  adreno_tzunload=1 (cmdline): the give-up call, early, with the channel proven."
echo "  if a boot dies early, before pulling the plug:"
echo "  fastboot oem log && fastboot get_staged r22-klog.txt   # every step's line is in there"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
