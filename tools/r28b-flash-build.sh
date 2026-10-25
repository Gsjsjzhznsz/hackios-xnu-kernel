#!/bin/bash
# tools/r28b-flash-build.sh -- pack the r28b SpringBoard boot images for raphael (K20 Pro, hackios).
#
# R28b = the identity-audit round, on the R28 GPU field report's charge ("the kernel
#   that ran was R27's").  The released R28 images were forensically PROVEN to carry
#   the R28 kernel (kernel-entry sha256 9e3dd447d1e92158b..., the R28 baseline marker
#   present, the retired R25/R25b strings absent -- a kernel cannot print a string its
#   binary does not carry, so the report's step-19 sweep/fuse lines were the R27b
#   boot's residue below the fresh record, past the flush head's L-byte boundary).
#   R28b makes the identity provable ON THE DEVICE and at build time, three ways:
#   1. the kernel stamps its own generation at the very TOP of its log -- "R28b: the
#      kernel generation line" rides the driver's first printf AND step 1's first
#      flush, so every record this kernel writes names its generation before any
#      step runs (older rounds' residue self-declares too: v5 loaders, R25/R26/R27
#      wordings).
#   2. step 19's baseline line rewords to "R28b: the two-parameter baseline" -- the
#      R28 wording joins the retired list, so the source move is gate-provable.
#   3. every packed image prints its kernel-entry sha256 AND the marker grep counts
#      (R28b baseline = 1, R28 baseline = 0, R25 sweeps = 0, R25b E7 = 0).
#   The experiment itself is UNCHANGED from R28 (the two-parameter baseline, the two
#   retirements, the cmdline channel, the logdump/flush discipline, all guards).
#   The loader stays v6 BYTE-IDENTICAL (its "R28 cmdline:" string is the v6 loader's
#   own frozen wording; the kernel generation is what the "R28b:" lines say).
#   R27b closed the shape charge (every three-word INIT_IMAGE dies at the SMC
#   itself, -5, never reaching TrustZone's verdict; the one surviving code --
#   E3's word 0x82 -- is the two-parameter shape's own answer, so the sweeps
#   carried zero information and real reset risk) and convicted the fuse sweep
#   (R25's t=4s death was E7's next 256-byte row; R25b's move to step 19's end
#   only moved the body, the trigger stayed).
#   1. adreno_e1_mode defaults to 2 (the size stays home) -- the shape that
#      answers; 3 stays askable by name, for the record.
#   2. adreno_qfprom defaults to 0 -- E7 rides only behind adreno_qfprom=1.
#   3. the shape sweeps (E2/E2-control/E3/E4/E5/E5b) ride only behind
#      adreno_shape_sweep=1 -- the default run goes from the zero-page controls
#      straight to the supports/give-up diagnostics.
#   4. the cmdline channel, said in full: the fastboot reply caps at 59 bytes
#      (the tail truncation R27b hit), so every cmdline verb now states its own
#      whole state in the loader's log (one "oem log" reads it), and every boot
#      states its own extras before the jump ("R28 cmdline: the extras riding
#      this boot: ...").
#   Kept verbatim: the logdump/keep/flush channel (R27's fetch fix, whole-log
#   notes to 60 s, the flush head), the empty probe, the zero-page controls,
#   the MEM_SETUP/AUTH_AND_RESET guards, step 1's first line, the step
#   20/21/22 entry guards, "the steps are over".
#   The loader moves v5 -> v6; the volume is byte-identical.
#
# Two images, one release, ONE kernel (the same source-built payload in both):
#   raphael-xnu-r28b-flash.img       touchscreen=1 buttons=1 cpus=8 -- the daily eight-core one
#   raphael-xnu-r28b-gpu-flash.img   the same, and gpu=1 -- the ladder, into step 19's water
#
# usage: tools/r28b-flash-build.sh [tree]        (default: this repository)
#   KERNEL=... ROOTAPFS=... FLASHBOOT=... OUT=... OUTGPU=...   overrides

set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}
OUT=${OUT:-$TREE/userland/raphael-xnu-r28b-flash.img}
OUTGPU=${OUTGPU:-$TREE/userland/raphael-xnu-r28b-gpu-flash.img}
CMDLINE='touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata'
CMDLINE_GPU="$CMDLINE gpu=1"

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r28b-flash-build: missing $f (kernel: built by xnu-build.sh -- see the workflow or xnu/README.md)"; exit 1; }
done
echo "r28b kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"

# The loader is the one this tree compiles (R10's, unchanged): compile it (clang + lld-link).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r28b-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
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
             b'oem logdump', b'no logdump partition',
             b'cores for the kernel, in the order it takes them',
             b'oem keep-logdump', b"the previous run's log is kept",
             b'asked for a flush', b'oem cmdline-append',
             b'XNULOADER v6',
             b'R28 cmdline: the extras riding this boot',
             b'== XNU FLUSH', b'the 2-second note',
             b'stop and arm first: oem keep-logdump'):
    if feat not in d:
        print(f'r28-flash-build: loader lacks the marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
if b'debug=0x8' in d:
    print('r28-flash-build: loader still carries debug=0x8')
    sys.exit(1)
print(f'r28b loader v6: {len(d):,} B (byte-identical to R28\'s -- the anchor that did NOT move; the kernel generation is the "R28b:" lines\' job)')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r28b-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r28b-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
echo "boot volume: $FLASHBOOT ($(stat -c %s "$FLASHBOOT") B, stage0 as sbin/launchd inside)"

# mkxnuimg has two ways to find the pieces: a Mu-Silicium tree (rebuilds the shim with make+clang,
# non-deterministic, needs a toolchain) or prebuilt/uefi/ (committed files). Force the latter:
if [ -d Mu-Silicium ]; then
        mv Mu-Silicium Mu-Silicium.hidden-by-r25
        HIDDEN=1
fi
cp prebuilt/uefi/BootShim.bin prebuilt/uefi/BootShim.redwood.keep
restore() {
        mv -f prebuilt/uefi/BootShim.redwood.keep prebuilt/uefi/BootShim.bin
        [ -z "${HIDDEN:-}" ] || mv Mu-Silicium.hidden-by-r25 Mu-Silicium
}
trap restore EXIT
cp "$R/BootShim.bin" prebuilt/uefi/BootShim.bin

export UEFI_TARGET=raphael UEFI_FD="$R/SILICIUM_UEFI-raphael.fd" DTB="$R/raphael.dtb"
export FD_BASE=0x9FC00000 FD_SIZE=0x300000 PAYLOAD_BASE=0xF0000000
export BOOTIMG_HEADER_VERSION=2 BOOTIMG_DTB_ADDR=0x1F00000

pack_one() {
        local name="$1" out="$2" cmdline="$3"
        python3 xnuloader/mkpayload.py -o /tmp/r28b-payload.bin \
                "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
                --cmdline "$cmdline" --usb
        python3 mkxnuimg.py -p /tmp/r28b-payload.bin -o "$out"
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
             b'oem logdump', b'no logdump partition',
             b'cores for the kernel, in the order it takes them',
             b'oem keep-logdump', b"the previous run's log is kept",
             b'asked for a flush', b'oem cmdline-append',
             b'XNULOADER v6',
             b'R28 cmdline: the extras riding this boot',
             b'== XNU FLUSH', b'the 2-second note',
             b'stop and arm first: oem keep-logdump'):
        if feat not in ents['loader.efi']:
                fail(f'packed loader lacks the marker {feat!r}')
if b'debug=0x8' in ents['loader.efi']:
        fail('packed loader still carries debug=0x8')
if ents['kernel'] != open(kernel_path, 'rb').read():
        fail('kernel identity broken (not the source-built r28b kernel)')
# R28b audit trail: each packed image prints its kernel-entry sha256 AND the marker
# grep results -- one kernel, two images, provable from the build log alone.  The R28
# GPU report's charge ("an R27 kernel slipped in") is answered here every build: the
# R28b baseline present, the R28/R25/R25b wordings absent.
print(f'kernel payload sha256 ({img_path}): {hashlib.sha256(ents["kernel"]).hexdigest()}')
for label, needle in (('R28b baseline (want 1)', b'R28b: the two-parameter baseline'),
                      ('R28  baseline (want 0)', b'R28: the two-parameter baseline'),
                      ('R25  shape sweeps (want 0)', b'R25: the shape sweeps'),
                      ('R25b E7 announce  (want 0)', b'R25b: E7 moved to the end of the refusal path'),
                      ('R28b generation line (want 2)', b'R28b: the kernel generation line')):
        print(f'  marker count, {label}: {ents["kernel"].count(needle)}')
if b'R28b: the two-parameter baseline' not in ents['kernel']:
        fail('the kernel entry lacks the R28b baseline marker -- an old kernel slipped in')
if b'R28: the two-parameter baseline' in ents['kernel']:
        fail('the kernel entry still carries the R28 baseline wording -- the R28b source edit did not take')
if ents['kernel'].count(b'R28b: the kernel generation line') < 2:
        fail('the kernel entry lacks its generation stamps (driver first line + step 1 first flush)')
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
             # R23 kept: the file's own verdict, one canonical attempt, the place, the controls
             b"THE SIGNATURE CHAIN SAYS",
             b'the hashes carry "',
             b"A TEST KEY (Qualcomm SecTools",
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
             b"no memory for the zap image",
             # R25: the production chain, named
             b"Xiaomi Attestation CA",
             b"Xiaomi Root CA",
             b"THE PHONE'S OWN PRODUCTION CHAIN",
             b"no test-key marks and no Xiaomi marks",
             # R25: the shape of the call itself -- R28 retires the sweeps behind
             # adreno_shape_sweep=1 / adreno_qfprom=1; the E2..E5 code rides for the record
             b"E2: the size sweep",
             b"E2 control: the hash table corrupted",
             b"E3: the argument word sweep",
             b"E4: the id sweep",
             b"E5: the same call again",
             b"E5: after the give-up",
             b"secboot and rollback fuses",
             # R26: the flush channel, the mode switch, the empty probe
             b"XNULIVE3",
             b"adreno_e1_mode",
             b"the empty probe",
             b"step 19 entered",
             b"goes to the storage first",
             b"did not put the log away in time",
             b"R26 E1, adreno_e1_mode=3",
             b"R26 E1, adreno_e1_mode=2",
             # R27: the channel's own test and the steps' guards
             b"step 1's first line: the flush channel's own test",
             b"step 20 entered: the 3D core's registers are next",
             b"step 21 entered: out of secure mode",
             b"step 22 entered: the first frame is next",
             b"the steps are over (R27)",
             # R28b: the generation stamps + the two-parameter baseline + the two retirements
             b"R28b: the kernel generation line",
             b"R28b: the two-parameter baseline",
             b"adreno_shape_sweep",
             b"R28: the shape sweeps, retired behind adreno_shape_sweep=1",
             b"R28: retired behind adreno_qfprom=1"):
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
             b'three attempts -- the whole .mbn as the metadata',
             # R23's two-way verdict retired by R25's three-way
             b"no test-key marks -- the chain reads like the phone's own",
             # R25b: E7's step-2 placement retired -- the early-death round
             b"read the secboot and rollback fuses (E7: the whole qfprom windows",
             # R26: E1's text now rides adreno_e1_mode's two states
             b"R25 E1: Linux's shape",
             # R28: the shape sweeps and the fuse sweep leave the default run
             b"R25: the shape sweeps",
             b"R25b: E7 moved to the end of the refusal path",
             # R28b: the R28 baseline wording retired by the R28b reword
             b"R28: the two-parameter baseline"):
        if gone in k:
                fail(f'the built kernel still carries a retired marker {gone!r} -- the round that deleted it was not applied')
if ents['ramdisk'] != open(rootapfs_path, 'rb').read():
        fail('ramdisk identity broken')
# R25: the md1 volume must carry the phone's own (Xiaomi-signed) zap file, and never the
# test-signed one -- the swap happened in the workflow; this is the packing gate on its result
v = open(rootapfs_path, 'rb').read()
if b'Xiaomi Attestation CA' not in v:
        fail('the md1 volume does not carry the Xiaomi-signed a640_zap.mbn -- the swap step did not run')
if b'Generated Test Attestation CA' in v:
        fail('the md1 volume still carries the TEST-SIGNED a640_zap.mbn -- the swap did not take')
# R25: the structural audit -- the volume proves itself (metadata checksums, extent
# ownership, the swap's non-overlap with step 18's and the GMU's firmware, the stock
# zap's in-place sha256).  A build with a structurally unsound volume refuses to ship.
import subprocess, sys as _sys
r = subprocess.run([_sys.executable, 'tools/r24b_volume_audit.py', 'gate', rootapfs_path])
if r.returncode != 0:
        fail('the md1 volume failed the R25 structural audit (tools/r24b_volume_audit.py gate)')
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
print(f'r28b image verified: {img_path}: {len(img):,} B | payload {len(payload):,} B | cmdline "{cmdline}"')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (v6, byte-identical to R28) '
      f'kernel sha {hashlib.sha256(k).hexdigest()[:16]} (SOURCE-BUILT r28b)')
print(f'  md1 zap chain: Xiaomi-signed (the swap took), test chain absent')
print(f'  shim {hashlib.sha256(shim).hexdigest()[:16]} '
      f'FD {hashlib.sha256(fd).hexdigest()[:16]} '
      f'DTB {hashlib.sha256(ref["raphael.dtb"]).hexdigest()[:16]}')
PY
}

pack_one main "$OUT" "$CMDLINE"
pack_one gpu  "$OUTGPU" "$CMDLINE_GPU"

echo
echo "boot images (ONE source-built kernel in both -- the build log above prints the kernel-entry"
echo "sha256 and the marker grep counts for each; tools/r28b-kernel-id.py <image> re-proves any"
echo "image offline, including R27/R28 releases):"
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
echo "BLACK-SCREEN FORENSICS FIRST (R27: the fetch is fixed -- oem logdump reads the"
echo "  PARTITION now, not the loader's ring; R26's 514-byte banner was the 'oem log'"
echo "  prefix swallowing 'oem logdump' in the command chain):"
echo "  fastboot oem logdump && fastboot get_staged r28b-logdump.bin"
echo "    -> the file holds the WHOLE logdump partition's first 4 MB: the flush head,"
echo "       then the loader log, then '== KERNEL LOG (the kernel is still running) ==',"
echo "       then the kernel's FULL log -- every line it printed up to the death."
echo "  (works after 'kernel returned'; while a kernel is still running use oem log.)"
echo ""
echo "the flush head (R27) -- read the fetched file at a glance:"
echo "  it starts with '== XNU FLUSH #N at Ts (why); L bytes follow ==' -- that is the"
echo "  LAST write that reached the partition, when it happened, and how much of what"
echo "  follows is one record; anything AFTER that L of bytes is an older, longer"
echo "  write's leftovers, not part of the record.  The whys you will see:"
echo "    'the kernel asked for a flush' -- a guard line went out before a call that"
echo "      can reset the phone; the partition holds everything up to that call."
echo "    'the 2-second note' -- the loader's own refresh; it now writes the WHOLE"
echo "      log too (R26's notes wrote only the loader's 16 KB ring on top of the"
echo "      kernel's full snapshot -- a death between two kernel flushes left only"
echo "      960 bytes of the kernel's side), and the notes run to 60 s now."
echo ""
echo "the cadence that keeps every run coverable (arm BEFORE each run that may reset):"
echo "    fastboot oem keep-logdump            # (alias: oem boot-no-clear)"
echo "    fastboot oem run                     # or the four-step boot"
echo "  -- the boot after a reset spends the magic (one shot) and writes NOTHING to"
echo "     the partition, so the dead run's log survives for the fetch:"
echo "    fastboot oem logdump && fastboot get_staged r28b-logdump.bin"
echo "  -- a boot that starts under the keep flag WARNS you on the screen before the"
echo "     kernel starts: that run writes nothing; arm first if it may reset."
echo ""
echo "what to read back (the R28 ladder):"
echo "  the partition's first kernel-side record (after the loader's part) is"
echo "  'step 1's first line: the flush channel's own test (R27)' -- it is on the"
echo "  storage before any step runs:"
echo "    . it is there, and later heads count up -- the channel works end to end;"
echo "      read the LAST flush head, then walk back: the newest complete record"
echo "      names the line the death came after."
echo "    . it is NOT there (and no 'did not put the log away in time' either) --"
echo "      the channel itself is what never worked; that is its own bug report."
echo "  step 19's R28b line is the round's own verdict: 'R28b: the two-parameter"
echo "    baseline -- adreno_e1_mode=2 (...), the shape sweeps behind"
echo "    adreno_shape_sweep=0, the fuse sweep behind adreno_qfprom=0' -- it names"
echo "    the three switches AS PARSED, so a stray cmdline shows itself there."
echo "  THE GENERATION GREP (residue killer): every record this kernel writes starts"
echo "    with 'R28b: the kernel generation line' (the driver's first line and step"
echo "    1's first flush).  grep -a 'R28b: the kernel' <logdump> hits ONLY this"
echo "    round's records; R27b-era residue self-declares as v5-loader + R25/R26/R27"
echo "    wordings.  Never read past the flush head's L bytes again."
echo "  the default refusal path is short now: 'step 19 entered', the empty probe,"
echo "    'E1 armed', the refusal and its code, the zero-page controls, the"
echo "    supports/give-up pair -- and DONE.  No E2..E5, no fuse rows: a record"
echo "    that shows them ran with adreno_shape_sweep=1 / adreno_qfprom=1."
echo "  the steps AFTER 19 have guards of their own (R27) -- find where the"
echo "    record ends:"
echo "    . ends before 'step 20 entered' -- the death sat between the end of"
echo "      step 19's refusal path and the first write to the 3D core itself."
echo "    . holds 'step 20 entered' but not 'step 21 entered' -- the 3D core's"
echo "      register writes (step 20) reset the phone."
echo "    . holds 'step 21 entered' but not 'step 22 entered' -- the command"
echo "      processor (CP_SET_SECURE_MODE -- the zap ask itself) or its first"
echo "      command did."
echo "    . holds 'step 22 entered' -- the first frame's registers did."
echo "    . holds 'the steps are over (R27)' -- the GPU ladder finished; the death"
echo "      was the system's own after it (the notes keep the record to 60 s)."
echo "  the switches (all runtime, no reflashing; the cmdline channel):"
echo "      fastboot oem cmdline-append adreno_e1_mode=3    # the three-word form, for the record"
echo "      fastboot oem cmdline-append adreno_shape_sweep=1  # the retired E2..E5 sweeps"
echo "      fastboot oem cmdline-append adreno_qfprom=1     # the retired fuse sweep"
echo "      fastboot oem cmdline                            # show the extras (full state also goes to oem log)"
echo "      fastboot oem cmdline-clear                      # drop them (the log states it in full)"
echo "      (any other kernel argument rides the same way; the room is 160)"
echo "  ⚠️ the fastboot REPLY caps at 59 bytes -- the tail truncation R27b hit;"
echo "     the WHOLE cmdline state is in the loader's log: fastboot oem log."
echo "     Every boot also prints 'R28 cmdline: the extras riding this boot: ...'"
echo "     before the jump -- each run's logdump names what actually rode."
echo "  adreno_tzunload=1 / adreno_zapmem / adreno_keep=0 沿用"
echo "  (加进 oem cmdline-append，不必再改 payload)。"
echo "  if a boot dies early, before pulling the plug:"
echo "  fastboot oem logdump && fastboot get_staged r28b-logdump.bin  # THE WHOLE kernel log"
echo "  fastboot oem log && fastboot get_staged r28b-klog.txt   # the loader's side + live tail"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
echo "  the MAIN image (no gpu=1) runs no GPU steps at all -- if it boots and the GPU image"
echo "  dies, the death is in the GPU path; the logdump names the line."
