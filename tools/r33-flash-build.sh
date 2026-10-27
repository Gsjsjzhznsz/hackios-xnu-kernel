#!/bin/bash
# tools/r33-flash-build.sh -- pack the r33 images for raphael (K20 Pro, hackios).
#
# R33 = the resident-GPU round: the GPU STAYS for the system to draw with.
#
# The R32 device verdict (the user's own logdump, the fork-exact form's first
# real test) reads like a bring-up green board:
#   step 12  "TRUSTZONE ACCEPTS THE ZAP SHADER'S SIGNATURE FROM THIS KERNEL"
#   step 19  "TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER"
#   step 21  "the page reads 0x600dc0de: THE 3D CORE WRITES OUR MEMORY ON COMMAND"
#   step 22  "A PICTURE DRAWN BY THE GPU IS ON THE PANEL: orange, white, blue"
# and then step 23 + the step-0 block tore ALL of it down (GMU slumber, the
# zap give-up, the SMMU teardown, CX collapse) -- "over; the system goes on
# starting" -- and SpringBoard drew in software ("no GPU draws").
#
# Why: the keep decision read only adreno_keep, and the R32 GPU phase's cmdline
# (gpu=1 adreno=22 adreno_pause=0 adreno_watch=0) carries none -- the header's
# own promise ("gpu=1 ... the GPU kept for the system to draw with") held only
# when adreno= was absent.  The fork of this very phone (HeliumStudio's
# raphael-bringup, the same XNU, the same A640) runs its daily GPU as plain
# gpu=1 -- the resident shape -- with GLES through Mesa confirmed on the panel.
#
# What moves in R33 (the kernel's own source, plus the cmdline):
#   raphael_adreno.c: the keep decision reads gpu=1 too (adreno_keep=0 is the
#   veto); the generation stamps go R32 -> R33; the R32 fork-exact print block
#   retires into the R33 resident-round line (its verdict is the device's own
#   record now).  The step-19 machinery, the vendor .mdt builder, the M2
#   archive, the N3 ring dump, the R30 sweep, the T2 controls: verbatim.
#   The GPU image's cmdline gains ONE token: adreno_keep=1 (appended, asserted).
#   The R32 kernel (44b5a2b9...) is the kernel being replaced; the loader is
#   still v7 byte-identical (sha ec15106d...); the volume still the R13 bytes
#   verbatim (sha 372c7fcb...).
#
# usage: tools/r33-flash-build.sh <r32-image> <r32-gpu-image>   (release assets)
#   KERNEL=...     override the R33 kernel (default: this tree's build)
#   OUTDIR=...     where the r33 images go (default: .)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R32 daily image}; B=${2:?the R32 gpu image}
OUTDIR=${OUTDIR:-$TREE}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}

[ -f "$KERNEL" ] || { echo "r33-flash-build: missing kernel $KERNEL (built by the workflow's step three -- see xnu/README.md)"; exit 1; }
echo "r33 kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"
mkdir -p "$OUTDIR"

# ---- the gate on the kernel this tree built: the round's own markers, the ones it keeps, and the ones it retires
python3 - "$KERNEL" "$TREE/fw-gpu-raphael/adsp.mbn" <<'PY'
import sys
k = open(sys.argv[1], 'rb').read()
for m, want in ((b'R33: the kernel generation line', 2),
                (b'R33: the resident round', 1),
                (b'THE GPU STAYS ON for the system to draw with', 1),
                (b'SCMDMP: R33 begins', 1),
                (b'SCMDMP QUE cpu%d: the SCM queue entry', 1),
                (b'SCMDMP HND cpu%d: the per-cpu handle cache entry', 1),
                (b'SCMDMP GLB: phys 0x858a5900', 1),
                (b'the SCM region, all %u bytes', 1),
                (b'SCMDMP TZREG: the TZ DIAG registry word', 1),
                (b"TZDIAG: the TZ's own log", 1),
                (b'SCMDMP HDREG: the hyp DIAG registry word', 1),
                (b"HYPDIAG: the hypervisor's own diag buffer", 1),
                (b'adreno_scmsweep', 2),
                (b'M2: seg %u: type', 1),
                (b'M2: the header rewritten canonical', 1),
                (b'M2: THE SIGNATURE CHAIN SAYS', 1),
                (b'HYPLOG: the registry at phys 0x146bfb30', 1),
                (b'HYPLOG: the ring, all %u bytes', 1),
                (b'R28k: the run goes on to the 3D core without the zap', 1),
                (b'R28j: the two-parameter baseline', 1),
                (b'R28j: the T2 control', 2),
                (b"the CPU monitor's override goes to every process", 1),
                (b'T2 VERDICT:', 3),
                (b"THE ADSP'S CHAIN SAYS", 1),
                (b'T2 armed', 1),
                (b'adreno_test_adsp', 4),
                (b"the kernel's own embedded copy", 2),
                (b'the volume is not read', 1),
                (b'the zap attempt follows in this same boot', 1),
                (b'R28j: the give-up right after the refusal', 1),
                (b'T2 page check', 1),
                (b'E1 page check', 1),
                (b"the adsp's hash segment's head", 1),
                (b"the zap's hash segment's head", 1),
                (b'the vendor .mdt is built', 1),
                (b'p_offset rides untouched at %#x', 1),
                (b'one attempt, the fork-exact form (R32: the vendor .mdt below', 1)):
    assert k.count(m) == want, f'the built kernel: {m!r} count {k.count(m)} != {want}'
for gone in (b'R32: the kernel generation line',
             b'R32: the fork-exact form -- a real vendor pair',
             b'SCMDMP: R32 begins',
             b'R31: the kernel generation line',
             b'R31: the fork-proven form',
             b'the packed metadata is built',
             b"the hash segment's p_offset rewritten",
             b'SCMDMP: R31 begins',
             b'the step stops here by design',
             b'R30: the kernel generation line',
             b'R30: the swallowed-code recovery',
             b'R28m: the kernel generation line',
             b'R28m: the M2 contract build + the N3 white-box probe',
             b'R28l: the kernel generation line',
             b'R28l: the phase bisection',
             b'R28k: the kernel generation line',
             b'R28j: the kernel generation line',
             b'R28i: the kernel generation line', b'R28i: the two-parameter baseline',
             b'R28i: the T2 control', b'INSTEAD of the zap attempt',
             b'R28g: the kernel generation line', b'R28g: the two-parameter baseline',
             b'R28g: the T2 control', b'R28d: the kernel generation line',
             b'R28d: the two-parameter baseline', b'no fw/adsp.mbn in the small image'):
    assert k.count(gone) == 0, f'the built kernel still carries the retired {gone!r}'
# the embedded .mdt itself must be in the binary, byte for byte
fw = open(sys.argv[2], 'rb').read()
assert k.find(fw) >= 0, 'the embedded adsp .mdt is NOT in the kernel binary'
print(f'r33-flash-build: kernel gate PASS ({len(k):,} B); R33 markers in (the resident round: '
      f'gpu=1 keeps, adreno_keep=1 rides the cmdline), the R32/R31/R30/R28* generation stamps out, '
      f'the step-19 vendor .mdt machinery + M2 archive + N3 + R30 sweep + T2 controls ride verbatim, '
      f'the adsp .mdt rides inside ({len(fw):,} B at file offset {k.find(fw):#x})')
PY

# ---- the surgery: the kernel entry everywhere, the cmdline entry in the GPU image only (+ adreno_keep=1)
python3 - "$A" "$B" "$KERNEL" "$OUTDIR" <<'PY'
import gzip, hashlib, os, struct, sys

src_daily, src_gpu, kernel_path, outdir = sys.argv[1:5]

def sha(b): return hashlib.sha256(b).hexdigest()

def parse_payload(p):
    assert p[:4] == b'XNUP', 'bad payload magic'
    size = struct.unpack('<I', p[4:8])[0]
    body = p[8:8 + size]
    ents, i = [], 0
    while i < len(body):
        name, esize, _ = struct.unpack('<16sII', body[i:i+24])
        if name.rstrip(b'\0') == b'': break
        ents.append([name.rstrip(b'\0').decode(), body[i+24:i+24+esize]])
        i += 24 + esize + (-esize % 16)
    return ents

def build_payload(ents):
    body = b''
    for name, data in ents:
        body += struct.pack('<16sII', name.encode(), len(data), 0) + data + b'\0' * (-len(data) % 16)
    body += b'\0' * 24
    return struct.pack('<II', 0x50554E58, len(body)) + body

V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'   # the device-validated v7
R32C = '44b5a2b97e2eeffa071c8b57c8a854985b1558aefe1b70f634e88e81b92c4f5e'  # the kernel being replaced (the r32 release's build)
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'  # the R13-lineage md1 bytes
GPU_TOKENS = 'gpu=1 adreno=22 adreno_pause=0 adreno_watch=0'   # the R32 GPU phase's token set (asserted in the base)
KEEP_TOKEN = 'adreno_keep=1'                                    # the ONE token R33 adds

kernel = open(kernel_path, 'rb').read()

def pack(src, out, gpu_image):
    img = open(src, 'rb').read()
    k_size = struct.unpack_from('<I', img, 8)[0]
    page = struct.unpack_from('<I', img, 36)[0]
    assert img[:8] == b'ANDROID!' and page == 4096
    r_size = struct.unpack_from('<I', img, 16)[0]
    dtb_size = struct.unpack_from('<I', img, 1648)[0]
    k_off = page
    r_off = (k_off + k_size + page - 1) // page * page
    d_off = (r_off + r_size + page - 1) // page * page
    assert d_off + dtb_size == len(img), 'truncation law'
    assert img[d_off:d_off+4] == b'\xd0\x0d\xfe\xed', 'dtb magic'
    ramdisk, dtb = img[r_off:r_off + r_size], img[d_off:d_off + dtb_size]

    blob = gzip.decompress(img[k_off:k_off + k_size])
    x = blob.find(b'XNUP')
    assert x > 0, 'no payload'
    ents = parse_payload(blob[x:])
    assert [n for n, _ in ents] == ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    loader, old_kernel, volume, cmdline = ents[0][1], ents[1][1], ents[2][1], ents[3][1]
    if old_kernel == kernel:
        raise SystemExit(f'{src}: already r33')
    # the base's own laws: the loader is the device-validated v7, the kernel is the
    # R32 build (the r32 release), the volume is the R13 lineage, and the daily base
    # cmdline carries no gpu/adreno token
    assert sha(loader) == V7, f'{src}: loader entry is not the device-validated v7 ({sha(loader)[:16]}...)'
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
    assert sha(old_kernel) == R32C, f'{src}: kernel entry is not the R32 kernel being replaced ({sha(old_kernel)[:16]}...)'
    assert sha(volume) == R13_VOLUME, f'{src}: the ramdisk entry is not the R13-lineage volume ({sha(volume)[:16]}...)'
    assert b'adreno_test_adsp' not in cmdline, f'{src}: the base cmdline already carries a T2 token?!'
    old_cmdline = cmdline.rstrip(b'\0').decode()
    ents[1][1] = kernel
    if gpu_image:
        # R33: the r32 GPU phase's token set rides in the base (asserted, never re-appended);
        # this round APPENDS exactly one token -- adreno_keep=1, the resident path's switch.
        toks = old_cmdline.split()
        for tok in GPU_TOKENS.split():
            assert tok in toks, f'{src}: the r32 gpu base cmdline lacks {tok}: {old_cmdline!r}'
        assert KEEP_TOKEN not in toks, f'{src}: the r32 gpu base cmdline already carries {KEEP_TOKEN}?!'
        new_cmdline = old_cmdline + ' ' + KEEP_TOKEN
        ents[3][1] = new_cmdline.encode()
    else:
        assert 'gpu=1' not in old_cmdline.split(), f'{src}: the daily base image unexpectedly carries gpu=1?!'
        assert 'adreno=' not in old_cmdline.split(), f'{src}: the daily base image unexpectedly carries an adreno token?!'
        assert KEEP_TOKEN not in old_cmdline.split(), f'{src}: the daily base image unexpectedly carries {KEEP_TOKEN}?!'
    blob_new = blob[:x] + build_payload(ents)
    blob_new += b'\0' * (-len(blob_new) % 16)
    kern = gzip.compress(blob_new, 6)

    out_bytes = bytearray(img[:page])
    struct.pack_into('<I', out_bytes, 8, len(kern))
    out_bytes += kern + b'\0' * (-len(kern) % page)
    out_bytes += ramdisk + b'\0' * (-len(ramdisk) % page)
    out_bytes += dtb
    open(out, 'wb').write(out_bytes)

    # ---- the packed image re-proves itself: every law, from the bytes alone
    img2 = open(out, 'rb').read()
    k2 = struct.unpack_from('<I', img2, 8)[0]
    r2 = struct.unpack_from('<I', img2, 16)[0]
    off = (4096 + k2 + 4095) // 4096 * 4096
    off = (off + r2 + 4095) // 4096 * 4096
    dtb2 = struct.unpack_from('<I', img2, 1648)[0]
    assert off + dtb2 == len(img2), 'truncation law'
    assert img2[off:off+4] == b'\xd0\x0d\xfe\xed', 'dtb magic'
    blob2 = gzip.decompress(img2[4096:4096 + k2])
    x2 = blob2.find(b'XNUP')
    ents2 = parse_payload(blob2[x2:])
    assert [n for n, _ in ents2] == ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    assert ents2[0][1] == loader, 'the loader moved'
    assert sha(ents2[0][1]) == V7
    assert ents2[1][1] == kernel, 'kernel identity broken'
    assert ents2[2][1] == volume, 'ramdisk identity broken: the volume must ride the R13 bytes'
    assert sha(ents2[2][1]) == R13_VOLUME, 'the ramdisk is not the R13-lineage bytes'
    packed = ents2[3][1].rstrip(b'\0').decode()
    if gpu_image:
        assert packed == old_cmdline + ' ' + KEEP_TOKEN, f'the gpu cmdline moved: {packed!r} (R33: exactly one token added)'
        toks = packed.split()
        for needle in GPU_TOKENS.split() + [KEEP_TOKEN]:
            assert needle in toks, f'the packed gpu cmdline lacks the token {needle}'
        assert 'adreno_test_adsp' not in toks, 'no T2 token may ride the cmdline this round'
        assert 'adreno_keep=0' not in toks, 'the veto must NOT ride the GPU image this round'
    else:
        assert packed == old_cmdline, f'the cmdline moved: {packed!r}'
        assert 'adreno=' not in packed.split(), 'the daily image must carry NO adreno token this round'
        assert 'adreno_test_adsp' not in packed.split(), 'no T2 token may ride the cmdline this round'
    for needle in ('touchscreen=1', 'buttons=1', 'cpus=8'):
        assert needle in packed.split(), f'the packed cmdline lacks the token {needle}'
    assert 'cpus=1' not in packed.split() and '-v' not in packed.split()
    assert len(blob2[x2:]) <= (64 << 20) - 0x10000, 'payload too big for the loader window'
    gpu = 'gpu=1' in packed.split()
    print(f'{out}: {len(img2):,} B')
    print(f'  image   {sha(img2)}')
    print(f'  loader  {sha(loader)[:16]}  (v7 VERBATIM from the R28c build, the device-validated bytes)')
    print(f'  kernel  {sha(kernel)[:16]}  (R33, replacing {sha(old_kernel)[:16]}... the R32 bytes)')
    print(f'  ramdisk {sha(volume)[:16]}  (R33: the R13-lineage volume VERBATIM via R31/R32 -- no rebuild, {len(volume):,} B)')
    if gpu_image:
        print(f'  cmdline "{packed}"  (R33: the r32 GPU tokens + adreno_keep=1 -- the resident path\'s switch)')
    else:
        print(f'  cmdline "{packed}"  (VERBATIM r32 daily; the daily image runs NO adreno step at all)')
    print(f'  dtb/outer-ramdisk: byte-identical to the r32 release image')
    return gpu

g1 = pack(src_daily, os.path.join(outdir, 'raphael-xnu-r33-flash.img'), False)
g2 = pack(src_gpu, os.path.join(outdir, 'raphael-xnu-r33-gpu-flash.img'), True)
assert (g1, g2) == (False, True), f'the gpu=1 token must ride exactly the GPU image (daily={g1}, gpu={g2})'
PY

# ---- both packed images carry the SAME kernel and the SAME untouched volume; the cmdline differs exactly by the phase tokens + the keep token
python3 - "$OUTDIR/raphael-xnu-r33-flash.img" "$OUTDIR/raphael-xnu-r33-gpu-flash.img" <<'PY'
import gzip, hashlib, struct, sys
def entries_of(path):
    img = open(path, 'rb').read()
    blob = gzip.decompress(img[4096:4096 + struct.unpack_from('<I', img, 8)[0]])
    x = blob.find(b'XNUP')
    size = struct.unpack('<I', blob[x+4:x+8])[0]
    i, out = x + 8, {}
    while i + 24 <= x + 8 + size and blob[i] != 0:
        name = blob[i:i+16].rstrip(b'\0').decode()
        esize = struct.unpack('<I', blob[i+16:i+20])[0]
        out[name] = blob[i+24:i+24+esize]
        i += 24 + ((esize + 15) & ~15)
    return out
a, b = (entries_of(p) for p in sys.argv[1:4][:2])
assert a['kernel'] == b['kernel'], 'the two images must carry the SAME R33 kernel (one kernel, both images)'
assert a['ramdisk'] == b['ramdisk'], 'the two images must carry the SAME untouched volume'
assert hashlib.sha256(a['ramdisk']).hexdigest() == '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4', \
    'the volume is not the R13-lineage bytes'
base = a['cmdline'].rstrip(b'\0').decode()
gpu = b['cmdline'].rstrip(b'\0').decode()
assert 'adreno=' not in base.split(), 'the daily image must carry NO adreno token'
assert gpu == base + ' gpu=1 adreno=22 adreno_pause=0 adreno_watch=0 adreno_keep=1', \
    f'the gpu cmdline must be the daily bytes + the R32 token set + adreno_keep=1, got: {gpu!r}'
print('r33-flash-build: one kernel in both images, the R13-lineage volume untouched in both; the cmdlines differ '
      'exactly by gpu=1 + the full-step tokens + adreno_keep=1 -- the resident switch is the round\'s ONE variable')
PY

echo
echo "r33 images:"
echo "  $OUTDIR/raphael-xnu-r33-flash.img       the daily image (no GPU steps; the R28k-proven clean control)"
echo "  $OUTDIR/raphael-xnu-r33-gpu-flash.img   the same + gpu=1 + adreno=22 adreno_pause=0 adreno_watch=0 adreno_keep=1"
echo "                                           (ALL steps, and the GPU STAYS: the teardown after step 22 does not run --"
echo "                                            'THE GPU STAYS ON for the system to draw with: the service RedwoodGPU')"
echo "phone side (the resident round, the GPU image first):"
echo "  fastboot boot  $OUTDIR/raphael-xnu-r33-gpu-flash.img"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage raphael-r32-flashboot.apfs   # the R13-lineage bytes, carried verbatim"
echo "  fastboot oem  run"
echo "  -> expect: 'steps up to 22 ... (R33: the kernel generation line)', the R32-proven step ladder"
echo "     (12 ACCEPTS / 19 AUTHENTICATED AND STARTED / 21 0x600dc0de / 22 the picture), then -- THE NEW LINE:"
echo "     'adreno: THE GPU STAYS ON for the system to draw with: the service RedwoodGPU', and NO"
echo "     'the GMU accepts \"slumber\"' / 'TrustZone has given it up' / CX-collapse block after it."
echo "  -> H-tree (WORKLOG preregistration):"
echo "     H1 = the STAYS-ON line + no teardown + the system alive = the resident path armed (the GPU answers"
echo "          RedwoodGPU; SpringBoard still software-renders -- the GL userspace is a later round, EXPECTED)"
echo "     H2 = a step fails or the keep line is absent -> send the whole logdump, the verdict reads the code"
echo "     H3 = the system dies with the GPU kept -> the boot-arg adreno_keep=0 is the one-token rollback"
echo "phone side (the control, optional): the same four steps with the daily image -- expect clean like every round's"
