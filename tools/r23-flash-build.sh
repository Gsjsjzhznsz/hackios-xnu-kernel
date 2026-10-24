#!/bin/bash
# tools/r23-flash-build.sh -- pack the r23 SpringBoard boot images for raphael (K20 Pro, hackios).
#
# R23 = the -3145797 refusal, DECODED.  R22's matrix closed the call's variables (the same code
#       under every metadata layout, every call form, every page).  The wild logs of this
#       platform's own Linux finish the job: on an sm8150-class phone, spss's PAS_INIT_IMAGE
#       dies with EXACTLY -3145797 (func id 0x42000201, "Initializing image failed(rc:-22)")
#       while cdsp/adsp/slpi/ipa_fws/venus/modem all pass the same boot, and a640_zap itself
#       comes up through the same three calls -- its place a DYNAMICALLY ALLOCATED page
#       ("loading from 0xfe500000 to 0xfe501000").  The code belongs to THE IMAGE, not the call.
#       And the a640_zap.mbn this tree has shipped since R13 (byte-identical inside every md1)
#       is signed with Qualcomm's SecTools TEST certificates ("Generated Test Attestation CA",
#       "General Use Test Key (for testing only)") -- a chain a fused phone's TrustZone refuses
#       from anyone.
#
#   KERNEL (xnu/files/bsd/dev/raphael_adreno.c):
#     - zap_chain_has(): bounded needle search over the hash blob.
#     - zap_header(): reads the certificate chain's own names out of the hashes BEFORE any call
#       and prints the verdict -- "THE SIGNATURE CHAIN SAYS: A TEST KEY ..." or "no test-key
#       marks -- the chain reads like the phone's own".
#     - step 19: ONE canonical attempt (the whole .mbn over a preemptible call -- both working
#       callers' form; the R22 matrix proved the code does not move with layout/form/page).
#     - the place: a FRESH PAGE of ours (the vendor's own shape; the dt's pil_gpu_region@0x98f15000
#       is referenced by nothing -- qcom,kgsl-hyp carries no memory-region at all).  Grant one
#       page (0x1000), write, cache-clean, AUTH_AND_RESET.  adreno_zapmem=<phys> forces the dt's
#       region back for an A/B.
#     - the refusal path asks THE CONTROLS: the same call over a page of ZEROES, for the GPU's
#       id and the audio's.  A different code = TrustZone READ our page (the refusal is about
#       the image); the same -3145797 = the refusal comes before any image is read (memory or
#       caller).  One boot, one verdict.
#   PACKAGING: unchanged from R17 (R13 volume bytes behind the eight content gates).
#
# Two images, one release:
#   raphael-xnu-r23-flash.img       touchscreen=1 buttons=1 cpus=8 -- the daily eight-core one
#   raphael-xnu-r23-gpu-flash.img   the same, and gpu=1 -- the ladder, into step 19's water
#
# usage: tools/r23-flash-build.sh [tree]        (default: this repository)
#   KERNEL=... ROOTAPFS=... FLASHBOOT=... OUT=... OUTGPU=...   overrides

set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}
OUT=${OUT:-$TREE/userland/raphael-xnu-r23-flash.img}
OUTGPU=${OUTGPU:-$TREE/userland/raphael-xnu-r23-gpu-flash.img}
CMDLINE='touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata'
CMDLINE_GPU="$CMDLINE gpu=1"

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r23-flash-build: missing $f (kernel: built by xnu-build.sh -- see the workflow or xnu/README.md)"; exit 1; }
done
echo "r23 kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"

# The loader is the one this tree compiles (R10's, unchanged): compile it (clang + lld-link).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r23-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
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
             b'cores for the kernel, in the order it takes them'):
    if feat not in d:
        print(f'r23-flash-build: loader lacks the marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
if b'debug=0x8' in d:
    print('r23-flash-build: loader still carries debug=0x8')
    sys.exit(1)
print(f'r23 loader: {len(d):,} B (R10 0.10, unchanged)')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r23-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r23-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r23
        HIDDEN=1
fi
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r23 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

pack_one() {
        local name="$1" out="$2" cmdline="$3"
        python3 xnuloader/mkpayload.py -o /tmp/r23-payload.bin \
                "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
                --cmdline "$cmdline" --usb
        python3 mkxnuimg.py -p /tmp/r23-payload.bin -o "$out"
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
        fail('kernel identity broken (not the source-built r23 kernel)')
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
             # R21: TrustZone, asked this phone's own way
             b"raphael's TrustZone took the processor with it",
             b"the give-up call is not sent by default",
             b"no zap shader can be asked for",
             b"the whole door is dark",
             b"adreno_tzunload",
             b"with the channel proven",
             b'whether it "supports" the GPU\'s image',
             b"the same of the audio's, for comparison",
             # R22 kept: the metadata and the form of the call
             b"the metadata is built as",
             b"the whole file, every p_offset at the real bytes (Linux's)",
             b"the call was preempted %d time%s before it was answered",
             b"a one-page metadata the way it is asked",
             # R23: the file's own verdict, one canonical attempt, the place, the controls
             b"THE SIGNATURE CHAIN SAYS",
             b'the hashes carry "',
             b"A TEST KEY (Qualcomm SecTools",
             b"no test-key marks -- the chain reads like the phone",
             b"one attempt, the form both working callers use",
             b"the R22 matrix proved the code does not move",
             b"the control: a page of zeroes as the GPU's metadata",
             b"control, the GPU's id over zeroes",
             b"control, the audio's id over the same zeroes",
             b"THE SAME CODE: the refusal comes BEFORE the image",
             b"A DIFFERENT ANSWER: TrustZone does read the buffer",
             b"the place is a fresh page of ours",
             b"dt's pil_gpu_region is referenced by nothing",
             b"adreno_zapmem",
             b"no memory for the zap image"):
        if feat not in k:
                fail(f'the built kernel lacks the marker {feat!r}')
# retired markers must be GONE: the rounds that deleted them
for gone in (b'of the stream table does not keep what is written',
             b'of the stream table instead',
             b'the free entries are offered one by one',
             b'refused every entry',
             b'the hypervisor drops SMR/S2CR',
             b'TAKEN TO MEAN THAT IT KEEPS A ZAP SHADER',
             # R22's matrix retired by R23 (the code proven independent of layout/form/page)
             b'attempt %u of 3',
             b"R21's packed metadata, a call that may be preempted",
             b'the whole .mbn as the metadata, a call "at once"',
             b'the give-up call takes the refusal back',
             b'attempt %u refused: the call returns',
             b'the next attempt goes on all the same',
             b'three attempts -- the whole .mbn as the metadata'):
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
print(f'r23 image verified: {img_path}: {len(img):,} B | payload {len(payload):,} B | cmdline "{cmdline}"')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R10 0.10, unchanged) '
      f'kernel sha {hashlib.sha256(k).hexdigest()[:16]} (SOURCE-BUILT r23)')
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
echo "  $OUTGPU ($(stat -c %s "$OUTGPU") bytes) -- + gpu=1 (the ladder, into step 19)"
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
echo "what to read back (the R23 ladder -- the refusal decoded, the file on trial):"
echo "  steps 1-18 are R21/R22's again (the channel probes answered):"
echo "    . 'THE SIGNATURE CHAIN SAYS: A TEST KEY (Qualcomm SecTools ...)' -- the file's own"
echo "      verdict.  The hashes carry Qualcomm's 'Generated Test Attestation CA' / 'General"
echo "      Use Test Key (for testing only)': a fused phone's TrustZone refuses that chain"
echo "      from anyone.  THE FILE IS THE PRIME SUSPECT, and the log has said so itself."
echo "    . 'one attempt, the form both working callers use' -> 'TrustZone returns 0, answer 0' ="
echo "      TAKEN (test key accepted -- an unfused/debug TZ, or the file was swapped): the place"
echo "      (a fresh page, adreno_zapmem can force the dt's region) -> the write -> 0xc2000205 ->"
echo "      'TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER' -> steps 20/21: NEW WATER."
echo "    . refused again (-3145797)?  THE CONTROLS decide what the code means:"
echo "        - 'control, the GPU's id over zeroes: ... A DIFFERENT ANSWER' = TrustZone READS"
echo "          our buffers fine -- the refusal of the real file is about THE IMAGE (its"
echo "          signature).  With the test-key verdict above: the file is the wall.  Replace"
echo "          fw/a640_zap.mbn with THIS phone's own signed file (stock ROM vendor.img,"
echo "          vendor/firmware/) and the chain should go green on the next run."
echo "        - '... THE SAME CODE' = the refusal comes BEFORE any image is read -- the memory"
echo "          or the caller.  The file question still stands, but the environment moves up:"
echo "          R24's battlefield (QHEE's view of our pages / the SHM-bridge note)."
echo "        - 'control, the audio's id over the same zeroes' tells image vs peripheral."
echo "    . all refused -> the diagnostics (supports GPU vs audio, then the give-up call) close"
echo "      the step, as in R21/R22; the system draws with the software renderer."
echo "  adreno_tzunload=1 (cmdline): the give-up call, early, with the channel proven."
echo "  adreno_zapmem=0x98f15000 (cmdline): force the dt's pil_gpu_region as the place (A/B)."
echo "  if a boot dies early, before pulling the plug:"
echo "  fastboot oem log && fastboot get_staged r23-klog.txt   # every step's line is in there"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
