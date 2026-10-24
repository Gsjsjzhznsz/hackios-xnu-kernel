#!/bin/bash
# tools/r26-flash-build.sh -- pack the r26 SpringBoard boot images for raphael (K20 Pro, hackios).
#
# R26 = the R25b battle report's four asks, made real.  R25b's GPU image ran to
#       "step 18: done" and reset the phone in the window after it (t=5..7 s);
#       step 19's prints were only in the RAM the reset ate, and the boot after
#       it wrote its own banner over whatever the loader had managed to flush.
#   Ask 1, the keeper: oem keep-logdump (alias oem boot-no-clear) writes a keep
#       magic into the logdump partition's LAST block -- where no composed log
#       ever lands (they start at block 0 and stay under 2 MB of 64 MB).  The
#       boot after a reset finds it, spends it (one shot) and writes nothing to
#       the partition the whole session: the log of the run that died survives
#       for "oem logdump".  Issuing oem keep-logdump again re-arms the next boot
#       AND turns this run's writes back on.
#   Ask 2, the flush channel (XNULIVE3, struct loader_live's third page): before
#       every call that can reset the phone, the kernel raises flush_seq; the
#       loader puts the WHOLE log (its side and the kernel's ring) on the
#       partition and answers with flush_ack.  13 guards in step 19: the step's
#       entry, E1's parameters, the refusal, every sweep family, MEM_SETUP,
#       AUTH_AND_RESET, and every row of the fuse sweep.
#   Ask 3, the A/B without reflashing: adreno_e1_mode (default 3 = Linux's
#       shape, the size goes with it -- R25b's, the one under trial; 2 =
#       R21-R24b's, the size stays home -- the control), set at runtime through
#       the loader's new "oem cmdline-append" (kernel arguments over USB, into
#       the next run's boot_args).
#   Ask 4, the empty probe: a question that hands TrustZone no memory (service 6
#       command 1 about INIT_IMAGE), sent right before E1 -- the anchor between
#       "step 19 was entered" and "E1 was armed".
#   The loader moves for the first time since 47911ca (banner v3 -> v4);
#   E1-E5, the zero-page controls and the fuse sweep ride verbatim R25b.
#
# Two images, one release:
#   raphael-xnu-r26-flash.img       touchscreen=1 buttons=1 cpus=8 -- the daily eight-core one
#   raphael-xnu-r26-gpu-flash.img   the same, and gpu=1 -- the ladder, into step 19's water
#
# usage: tools/r26-flash-build.sh [tree]        (default: this repository)
#   KERNEL=... ROOTAPFS=... FLASHBOOT=... OUT=... OUTGPU=...   overrides

set -e
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R=prebuilt/raphael
ROOTAPFS=${ROOTAPFS:-$TREE/userland/root.apfs}
FLASHBOOT=${FLASHBOOT:-$TREE/userland/flashboot.apfs}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}
OUT=${OUT:-$TREE/userland/raphael-xnu-r26-flash.img}
OUTGPU=${OUTGPU:-$TREE/userland/raphael-xnu-r26-gpu-flash.img}
CMDLINE='touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata'
CMDLINE_GPU="$CMDLINE gpu=1"

for f in "$R/BootShim.bin" "$R/SILICIUM_UEFI-raphael.fd" "$R/raphael.dtb" \
         "$KERNEL" "$ROOTAPFS" "$FLASHBOOT" xnuloader/loader.c xnuloader/build.sh \
         xnuloader/mkpayload.py mkxnuimg.py prebuilt/uefi/mkbootimg.py prebuilt/uefi/ramdisk; do
        [ -f "$f" ] || { echo "r26-flash-build: missing $f (kernel: built by xnu-build.sh -- see the workflow or xnu/README.md)"; exit 1; }
done
echo "r26 kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"

# The loader is the one this tree compiles (R10's, unchanged): compile it (clang + lld-link).
bash xnuloader/build.sh
LOADER=$TREE/xnuloader/loader.efi
[ -f "$LOADER" ] || { echo "r26-flash-build: xnuloader/loader.efi did not appear"; exit 1; }
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
             b'XNULOADER v4'):
    if feat not in d:
        print(f'r26-flash-build: loader lacks the marker {feat!r} -- built from the wrong tree?')
        sys.exit(1)
if b'debug=0x8' in d:
    print('r26-flash-build: loader still carries debug=0x8')
    sys.exit(1)
print(f'r26 loader v4: {len(d):,} B (keep-logdump + the flush channel + cmdline extras)')
PY

M=$(python3 -c "print(open('$ROOTAPFS','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || echo "warning: $ROOTAPFS has no NXSB at +32 (magic 0x$M) -- build.sh output changed?"
M=$(python3 -c "print(open('$FLASHBOOT','rb').read(36)[32:36].hex())")
[ "$M" = "4e585342" ] || { echo "r26-flash-build: $FLASHBOOT has no NXSB at +32 (magic 0x$M)"; exit 1; }
7z l "$FLASHBOOT" 2>/dev/null | grep -q 'launchd' \
        || { echo "r26-flash-build: $FLASHBOOT does not carry sbin/launchd (stage0)"; exit 1; }
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
        python3 xnuloader/mkpayload.py -o /tmp/r26-payload.bin \
                "loader.efi=$LOADER" "kernel=$KERNEL" "ramdisk=$ROOTAPFS" \
                --cmdline "$cmdline" --usb
        python3 mkxnuimg.py -p /tmp/r26-payload.bin -o "$out"
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
             b'XNULOADER v4'):
        if feat not in ents['loader.efi']:
                fail(f'packed loader lacks the marker {feat!r}')
if b'debug=0x8' in ents['loader.efi']:
        fail('packed loader still carries debug=0x8')
if ents['kernel'] != open(kernel_path, 'rb').read():
        fail('kernel identity broken (not the source-built r25 kernel)')
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
             # R25: the shape of the call itself
             b"R25: the shape sweeps",
             b"E2: the size sweep",
             b"E2 control: the hash table corrupted",
             b"E3: the argument word sweep",
             b"E4: the id sweep",
             b"E5: the same call again",
             b"E5: after the give-up",
             b"secboot and rollback fuses",
             b"R25b: E7 moved to the end of the refusal path",
             # R26: the flush channel, the mode switch, the empty probe
             b"XNULIVE3",
             b"adreno_e1_mode",
             b"the empty probe",
             b"step 19 entered",
             b"goes to the storage first",
             b"did not put the log away in time",
             b"R26 E1, adreno_e1_mode=3",
             b"R26 E1, adreno_e1_mode=2"):
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
             b"R25 E1: Linux's shape"):
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
print(f'r26 image verified: {img_path}: {len(img):,} B | payload {len(payload):,} B | cmdline "{cmdline}"')
print(f'  loader sha {hashlib.sha256(loader).hexdigest()[:16]} (R10 0.10 + oem logdump) '
      f'kernel sha {hashlib.sha256(k).hexdigest()[:16]} (SOURCE-BUILT r26)')
print(f'  md1 zap chain: Xiaomi-signed (the swap took), test chain absent')
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
echo "BLACK-SCREEN FORENSICS FIRST (R25's new channel -- any death, any round):"
echo "  fastboot oem logdump && fastboot get_staged r26-logdump.bin"
echo "    -> the file holds the LOADER log, then '== KERNEL LOG ==', then the kernel's"
echo "       FULL log -- every line it printed up to the death, panics included"
echo "       (R25's black screen had 105330 bytes there and no way to fetch them)."
echo "  (works after 'kernel returned'; while a kernel is still running use oem log.)"
echo ""
echo "what to read back (the R26 ladder):"
echo "  arm the keeper BEFORE the run that may reset the phone:"
echo "    fastboot oem keep-logdump            # (alias: oem boot-no-clear)"
echo "  -- the keep magic goes into the logdump partition's last block; the next"
echo "     boot spends it (one shot) and writes NOTHING to the partition, so the log"
echo "     of the run that died survives for the fetch:"
echo "    fastboot oem logdump && fastboot get_staged r26-logdump.bin"
echo "  the kernel asks for the log on the storage before every call that can reset"
echo "  the phone ('the whole log goes to the storage first' around each one); what"
echo "  the partition holds at the end names the line the death came after:"
echo "    . ends BEFORE 'step 19 entered' -- the death came before any call of the"
echo "      step was issued (not E1: the step 18 -> 19 transition; send it whole)."
echo "    . holds the empty probe's answer but ends at 'E1 armed' -- E1's own SMC"
echo "      reset the phone: rerun with adreno_e1_mode=2 -- two-param E1 (R24b's"
echo "      shape) returning -3145797 with the box alive closes the case: the SHAPE"
echo "      (the size word) is what flips TZ from a refusal to a reset."
echo "    . E1 answered (any code) -- the code, then the family guards name the rest:"
echo "      the refusal, the sweeps (E2/E2-control/E3/E4/E5/E5b), MEM_SETUP,"
echo "      AUTH_AND_RESET, and every row of the fuse sweep."
echo "  adreno_e1_mode (default 3): 3 = Linux's shape, the size goes with it;"
echo "    2 = R21-R24b's, the size stays home.  Set at runtime, no reflashing:"
echo "      fastboot oem cmdline-append adreno_e1_mode=2    # the control run"
echo "      fastboot oem cmdline                            # show the extras"
echo "      fastboot oem cmdline-clear                      # drop them"
echo "      (any other kernel argument rides the same way; the room is 160)"
echo "  adreno_qfprom=0 / adreno_tzunload=1 / adreno_zapmem / adreno_keep=0 沿用"
echo "  (加进 oem cmdline-append，不必再改 payload)。"
echo "  if a boot dies early, before pulling the plug:"
echo "  fastboot oem logdump && fastboot get_staged r26-logdump.bin  # THE WHOLE kernel log"
echo "  fastboot oem log && fastboot get_staged r26-klog.txt   # the loader's side + live tail"
echo "  the checkpoint ladder still bisects anything else: oem run 15, 16, 17, 18, 19, 20, 21"
echo "  the MAIN image (no gpu=1) runs no GPU steps at all -- if it boots and the GPU image"
echo "  dies, the death is in the GPU path; the logdump names the line."
