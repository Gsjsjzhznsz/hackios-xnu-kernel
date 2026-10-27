#!/bin/bash
# tools/r28m-flash-build.sh -- pack the r28m images for raphael (K20 Pro, hackios).
#
# R28m = the M2-contract + N3-probe round.  The R28l incident, first: run
# 40331159223 never produced an image -- THIS script's predecessor died in its
# own closing assert (it compared the GPU product against the DAILY base and
# forgot the GPU base's gpu=1), while the kernel itself built clean.  The
# phase bisection the R28l cmdline carried was never executed; R28m replaces
# the round entirely.  The R28k verdicts: (a) K4 hit, and between
# K1 and K2 a new leaf -- without the zap the 3D core lives and the CP eats ring
# commands (step 20 passed, R28-first) but stays locked out of secure mode (step
# 21: ring consumed, memwrite never lands, 0x800005) -- the zap is the hard
# prerequisite for secure-mode egress, not for core survival; (b) the lsd SIGBUS
# trigger face IS the adreno phase execution: the R28k main image (the phase
# skipped whole) ran clean per the operator's own observation (no white apple),
# while the R28k GPU image storms the lsd (first death lsd[29] 00:01:46.623310,
# ms-synced with backboardd's mapdb fetch failure, 84 deaths, counter peak 78).
# One kernel, one volume, one lsd binary -- the pre-registered left branch hit,
# the volume face is out.
#
# What moves in R28m (the kernel's own source, plus the GPU image's cmdline):
#   the kernel builds the zap's metadata page BY THE T3-BATCH3 CONTRACT instead
#   of a blind copy (zap_header_m2: every segment at its own declared p_offset,
#   the canonical ELF header fields, the hash segment's 0x02000000 mark
#   REQUIRED) and, at any refusal, dumps the QHEE's log ring (hyp_log_dump:
#   the registry at phys 0x146bfb30 -> the ring at 0x858e0000, magic "mpyh")
#   BEFORE the give-up pushes over the swallowed true code.  The GPU image runs
#   ALL steps:
#
#       adreno=22 adreno_pause=0 adreno_watch=0
#
#   (no adreno_skip -- the M2 page and the N3 dump live at the zap's step 19;
#   the announce line's "steps up to 22, without those in mask 0x0" is the
#   on-device probe that the full-phase landing happened.)
#   The daily image's cmdline rides verbatim (no adreno= token at all).
#
# The kernel's own changes are the R28m generation stamps (the announce line and
# the step-1 flush self-test), the M2+N3 announce line, zap_header_m2,
# hyp_log_dump and the two call-site swaps.  The R28k run-goes-on wording rides
# verbatim (the step-19 refusal path REACHES it this round); the T2 control
# strings all stay.  gpu_block_stop() is flag-driven.
#
# THE SURGERY is the R28i discipline: the two R28d images (the device-validated
# base) get their kernel entry replaced by this tree's R28m build -- and the GPU
# image's cmdline entry gets the phase tokens.  The ramdisk entry rides the R28d
# bytes verbatim (no rebuild, no adsp.mbn in the volume -- the .mdt rides inside
# the kernel), the loader rides VERBATIM (v7, ec15106d...), and the DTB / usb
# entry / header fields / truncation law are carried unchanged.  Two variables
# move this round, both in the GPU image: the kernel entry (the stamps) and the
# cmdline entry (the phase tokens); the daily image moves its kernel entry only.
#
# usage: tools/r28m-flash-build.sh <r28d-image> <r28d-gpu-image>   (release assets)
#   KERNEL=...     override the R28m kernel (default: this tree's build)
#   OUTDIR=...     where the r28m images go (default: .)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R28d daily image}; B=${2:?the R28d gpu image}
OUTDIR=${OUTDIR:-$TREE}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}

[ -f "$KERNEL" ] || { echo "r28m-flash-build: missing kernel $KERNEL (built by the workflow's step three -- see xnu/README.md)"; exit 1; }
echo "r28m kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"
mkdir -p "$OUTDIR"

# ---- the gate on the kernel this tree built: the round's own markers, the ones it keeps, and the ones it retires
python3 - "$KERNEL" "$TREE/fw-gpu-raphael/adsp.mbn" <<'PY'
import sys
k = open(sys.argv[1], 'rb').read()
for m, want in ((b'R28m: the kernel generation line', 2),
                (b'R28m: the M2 contract build + the N3 white-box probe', 1),
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
                (b"the zap's hash segment's head", 1)):
    assert k.count(m) == want, f'the built kernel: {m!r} count {k.count(m)} != {want}'
for gone in (b'the step stops here by design',
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
print(f'r28m-flash-build: kernel gate PASS ({len(k):,} B); R28m markers in (the phase bisection), '
      f'the R28k generation stamp out (its run-goes-on wording rides verbatim, the path waits), '
      f'R28j/R28i/R28g/R28d wordings squared, the adsp .mdt rides inside ({len(fw):,} B at file offset {k.find(fw):#x})')
PY

# ---- the surgery: the kernel entry everywhere, the cmdline entry in the GPU image only
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
R28D = '99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603'  # the kernel being replaced
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'  # the R13..R28d md1 bytes

# R28m's tokens: ALL steps (adreno=22 -- the zap's step 19 runs; M2's whole point
# dies without it), no skip, and the R28k-era no-pause/no-watch shape (the
# static pause/watch defaults would stall the run: the R28l discovery).  The
# announce line prints "steps up to 22, without those in mask 0x0" -- the
# on-device probe that the full-phase landing happened.
GPU_PHASE_TOKENS = ' adreno=22 adreno_pause=0 adreno_watch=0'

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
        raise SystemExit(f'{src}: already r28m')
    # the base's own laws: the loader is the device-validated v7, the kernel is R28d,
    # the volume is the R13 lineage, and the base cmdline carries no T2 token
    assert sha(loader) == V7, f'{src}: loader entry is not the device-validated v7 ({sha(loader)[:16]}...)'
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
    assert sha(old_kernel) == R28D, f'{src}: kernel entry is not the R28d kernel being replaced ({sha(old_kernel)[:16]}...)'
    assert sha(volume) == R13_VOLUME, f'{src}: the ramdisk entry is not the R13..R28d volume ({sha(volume)[:16]}...)'
    assert b'adreno_test_adsp' not in cmdline, f'{src}: the base cmdline already carries a T2 token?!'
    old_cmdline = cmdline.rstrip(b'\0').decode()
    ents[1][1] = kernel
    # R28m: the GPU image's cmdline carries the phase tokens; the daily image's
    # cmdline rides verbatim (its adreno phase is skipped whole by gpu's absence).
    if gpu_image:
        assert 'adreno=' not in old_cmdline.split(), f'{src}: the base gpu cmdline already carries an adreno token?!'
        new_cmdline = (old_cmdline + GPU_PHASE_TOKENS).encode()
        ents[3][1] = new_cmdline
    else:
        assert 'gpu=1' not in old_cmdline.split(), f'{src}: the daily base image unexpectedly carries gpu=1?!'
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
    assert ents2[2][1] == volume, 'ramdisk identity broken: the volume must ride the R28d bytes'
    assert sha(ents2[2][1]) == R13_VOLUME, 'the ramdisk is not the R13..R28d volume'
    packed = ents2[3][1].rstrip(b'\0').decode()
    if gpu_image:
        assert packed == old_cmdline + GPU_PHASE_TOKENS, f'the gpu cmdline is not base+phase-tokens: {packed!r}'
        toks = packed.split()
        for needle in ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0'):
            assert needle in toks, f'the packed gpu cmdline lacks the token {needle}'
        assert 'adreno_test_adsp' not in toks, 'no T2 token may ride the cmdline this round'
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
    print(f'  loader  {sha(loader)[:16]}  (v7 VERBATIM from the R28d release, the device-validated build)')
    print(f'  kernel  {sha(kernel)[:16]}  (R28m, replacing {sha(old_kernel)[:16]}... the R28d bytes)')
    print(f'  ramdisk {sha(volume)[:16]}  (R28m: the R28d volume VERBATIM -- no rebuild, no adsp.mbn, {len(volume):,} B)')
    if gpu_image:
        print(f'  cmdline "{packed}"  (R28m: base + gpu=1 + the full-step tokens -- ALL steps, the zap\'s step 19 included)')
    else:
        print(f'  cmdline "{packed}"  (VERBATIM R28d; the daily image runs NO adreno step at all)')
    print(f'  dtb/outer-ramdisk: byte-identical to the R28d release image')
    return gpu

g1 = pack(src_daily, os.path.join(outdir, 'raphael-xnu-r28m-flash.img'), False)
g2 = pack(src_gpu, os.path.join(outdir, 'raphael-xnu-r28m-gpu-flash.img'), True)
assert (g1, g2) == (False, True), f'the gpu=1 token must ride exactly the GPU image (daily={g1}, gpu={g2})'
PY

# ---- both packed images carry the SAME kernel and the SAME untouched volume; the cmdline differs exactly by the phase tokens
python3 - "$OUTDIR/raphael-xnu-r28m-flash.img" "$OUTDIR/raphael-xnu-r28m-gpu-flash.img" <<'PY'
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
assert a['kernel'] == b['kernel'], 'the two images must carry the SAME R28m kernel (one kernel, both images)'
assert a['ramdisk'] == b['ramdisk'], 'the two images must carry the SAME untouched volume'
assert hashlib.sha256(a['ramdisk']).hexdigest() == '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4', \
    'the volume is not the R13..R28d bytes'
base = a['cmdline'].rstrip(b'\0').decode()
gpu = b['cmdline'].rstrip(b'\0').decode()
assert 'adreno=' not in base.split(), 'the daily image must carry NO adreno token'
assert gpu == base + ' gpu=1 adreno=22 adreno_pause=0 adreno_watch=0', \
    f'the gpu cmdline must be the daily bytes + gpu=1 + the full-step tokens, got: {gpu!r}'
print('r28m-flash-build: one kernel in both images, the R28d volume untouched in both; the cmdlines differ '
      'exactly by gpu=1 + the full-step tokens -- the R28l assert that killed that round is fixed: '
      'the GPU base\'s own gpu=1 is part of the expected shape')
PY

echo
echo "r28m images:"
echo "  $OUTDIR/raphael-xnu-r28m-flash.img       the daily image (no GPU steps; the lsd/SIGBUS control, R28k-proven clean)"
echo "  $OUTDIR/raphael-xnu-r28m-gpu-flash.img   the same + gpu=1 + adreno=22 adreno_pause=0 adreno_watch=0"
echo "                                           (ALL steps -- the M2 page build and the N3 ring dump live at step 19)"
echo "phone side (the bisection round, the GPU image first):"
echo "  fastboot boot  $OUTDIR/raphael-xnu-r28m-gpu-flash.img"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage raphael-r28m-flashboot.apfs   # the R28d bytes (= R13), carried verbatim"
echo "  fastboot oem  run"
echo "  -> expect: 'R28m: the M2 contract build + the N3 white-box probe' announce, 'steps up to 22 ... mask 0x0'"
echo "     (the on-device probe that the full-phase landing happened), then the M2 seg table, the T2"
echo "     control, the E1 attempt -- on a refusal: the HYPLOG ring dump right after the refusal line,"
echo "     flushed BEFORE the give-up -- then the run goes on to the 3D core (R28k)"
echo "  -> M/N-tree (WORKLOG preregistration): E1 ACCEPTED = the wall falls; else read the ring"
echo "phone side (the control, optional): the same four steps with the daily image -- expect clean like R28k's"
