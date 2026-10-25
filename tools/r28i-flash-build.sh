#!/bin/bash
# tools/r28i-flash-build.sh -- pack the r28i images for raphael (K20 Pro, hackios).
#
# R28i = the T2 control, second edition -- after R28G's first-second death and
# R28h-B's two verdicts:
#
#   R28h-B (this same kernel on the UNTOUCHED R28d volume) proved two things:
#     the kernel lives to 122 s and userspace (so the R28G death was the
#     REBUILT volume's, not the kernel's), and the lsd/analyticsd/mobilegestalt
#     SIGBUS storm is real on the R28g-generation binary (R28d's own kernel
#     never showed it) -- prime suspect: the build environment (the floating
#     theos/xnu-deps cache legs under r25-*-v1 keys, and the runner's apt
#     clang), which is why the r28i workflow pins the toolchain (L1ghtmann
#     v19.0.0-L2 by name, fresh cache keys with NO restore chain) and records
#     every compiler's full version into the release for the audit.
#
#   THE KERNEL (raphael_adreno.c + raphael_adsp_fw.h, new): the T2 control is
#     ON BY DEFAULT now -- the flasher types no arguments (R28h-B rode an old
#     cmdline without adreno_test_adsp=1 and the round was lost to it), so the
#     default IS the round; adreno_test_adsp=0 turns it off by name.  And the
#     adsp's .mdt rides INSIDE the kernel (the redwood_focaltech_fw.h
#     discipline): the volume is never asked (the R28g volume rebuild was the
#     death), so T2's data source has zero volume variables.
#
#   THE SURGERY on the two R28d images (the device-validated base): the kernel
#     entry is replaced by this tree's R28i build -- AND NOTHING ELSE.  The
#     ramdisk entry rides the R28d bytes verbatim (no rebuild, no adsp.mbn in
#     the volume), the cmdline rides verbatim (NO adreno_test_adsp token: the
#     switch is the default now; the daily image keeps its no-gpu cmdline and
#     the GPU image keeps gpu=1, exactly the R28d pair), the loader rides
#     VERBATIM (v7, ec15106d...), and the DTB / usb entry / header fields /
#     truncation law are carried unchanged.  One variable moves this round:
#     the kernel entry.  The T2 control runs in the GPU image (gpu=1 starts
#     the adreno steps; the daily image never reaches step 19) -- which makes
#     the daily image the clean lsd/SIGBUS control: same kernel binary, no
#     GPU steps, so a SIGBUS storm on the daily image indicts the BUILD, and
#     a calm daily plus a storm on the GPU image indicts the GPU steps.
#
# usage: tools/r28i-flash-build.sh <r28d-image> <r28d-gpu-image>   (release assets)
#   KERNEL=...     override the R28i kernel (default: this tree's build)
#   OUTDIR=...     where the r28i images go (default: .)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R28d daily image}; B=${2:?the R28d gpu image}
OUTDIR=${OUTDIR:-$TREE}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}

[ -f "$KERNEL" ] || { echo "r28i-flash-build: missing kernel $KERNEL (built by the workflow's step three -- see xnu/README.md)"; exit 1; }
echo "r28i kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"
mkdir -p "$OUTDIR"

# ---- the gate on the kernel this tree built: the round's own markers, the ones it keeps, and the ones it retires
python3 - "$KERNEL" "$TREE/fw-gpu-raphael/adsp.mbn" <<'PY'
import sys
k = open(sys.argv[1], 'rb').read()
for m, want in ((b'R28i: the kernel generation line', 2),
                (b'R28i: the two-parameter baseline', 1),
                (b'R28i: the T2 control', 2),
                (b"the CPU monitor's override goes to every process", 1),
                (b'T2 VERDICT:', 3),
                (b"THE ADSP'S CHAIN SAYS", 1),
                (b'T2 armed', 1),
                (b'adreno_test_adsp', 4),
                (b"the kernel's own embedded copy", 2),
                (b'the volume is not read', 1),
                (b'the step stops here by design', 1)):
    assert k.count(m) == want, f'the built kernel: {m!r} count {k.count(m)} != {want}'
for gone in (b'R28g: the kernel generation line', b'R28g: the two-parameter baseline',
             b'R28g: the T2 control', b'R28d: the kernel generation line',
             b'R28d: the two-parameter baseline', b'no fw/adsp.mbn in the small image'):
    assert k.count(gone) == 0, f'the built kernel still carries the retired {gone!r}'
# the embedded .mdt itself must be in the binary, byte for byte
fw = open(sys.argv[2], 'rb').read()
assert k.find(fw) >= 0, 'the embedded adsp .mdt is NOT in the kernel binary'
print(f'r28i-flash-build: kernel gate PASS ({len(k):,} B); R28i markers in, R28g/R28d wordings out, '
      f'the adsp .mdt rides inside ({len(fw):,} B at file offset {k.find(fw):#x})')
PY

# ---- the surgery: the kernel entry alone
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

kernel = open(kernel_path, 'rb').read()

def pack(src, out):
    img = open(src, 'rb').read()
    k_size = struct.unpack('<I', img[8:12])[0]
    page = struct.unpack('<I', img[36:40])[0]
    assert img[:8] == b'ANDROID!' and page == 4096
    r_size = struct.unpack('<I', img[16:20])[0]
    dtb_size = struct.unpack('<I', img[1648:1652])[0]
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
        raise SystemExit(f'{src}: already r28i')
    # the base's own laws: the loader is the device-validated v7, the kernel is R28d,
    # the volume is the R13 lineage, and the cmdline carries no T2 token (R28i's switch
    # is the default -- the cmdline rides verbatim this round, both images)
    assert sha(loader) == V7, f'{src}: loader entry is not the device-validated v7 ({sha(loader)[:16]}...)'
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
    assert sha(old_kernel) == R28D, f'{src}: kernel entry is not the R28d kernel being replaced ({sha(old_kernel)[:16]}...)'
    assert sha(volume) == R13_VOLUME, f'{src}: the ramdisk entry is not the R13..R28d volume ({sha(volume)[:16]}...)'
    assert b'adreno_test_adsp' not in cmdline, f'{src}: the base cmdline already carries a T2 token?!'
    old_cmdline = cmdline.rstrip(b'\0').decode()
    ents[1][1] = kernel
    # NOTHING else moves: no ramdisk swap, no cmdline edit -- R28i is the kernel alone
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
    k2 = struct.unpack('<I', img2[8:12])[0]
    r2 = struct.unpack('<I', img2[16:20])[0]
    off = (4096 + k2 + 4095) // 4096 * 4096
    off = (off + r2 + 4095) // 4096 * 4096
    dtb2 = struct.unpack('<I', img2[1648:1652])[0]
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
    assert packed == old_cmdline, f'the cmdline moved: {packed!r}'
    assert 'adreno_test_adsp' not in packed.split(), 'no T2 token may ride the cmdline this round'
    for needle in ('touchscreen=1', 'buttons=1', 'cpus=8'):
        assert needle in packed.split(), f'the packed cmdline lacks the token {needle}'
    assert 'cpus=1' not in packed.split() and '-v' not in packed.split()
    assert len(blob2[x2:]) <= (64 << 20) - 0x10000, 'payload too big for the loader window'
    gpu = 'gpu=1' in packed.split()
    print(f'{out}: {len(img2):,} B')
    print(f'  image   {sha(img2)}')
    print(f'  loader  {sha(loader)[:16]}  (v7 VERBATIM from the R28d release, the device-validated build)')
    print(f'  kernel  {sha(kernel)[:16]}  (R28i, replacing {sha(old_kernel)[:16]}... the R28d bytes)')
    print(f'  ramdisk {sha(volume)[:16]}  (R28i: the R28d volume VERBATIM -- no rebuild, no adsp.mbn, {len(volume):,} B)')
    print(f'  cmdline "{packed}"  (VERBATIM; the T2 switch is the kernel default now, no token needed)')
    print(f'  dtb/outer-ramdisk: byte-identical to the R28d release image')
    return gpu

g1 = pack(src_daily, os.path.join(outdir, 'raphael-xnu-r28i-flash.img'))
g2 = pack(src_gpu, os.path.join(outdir, 'raphael-xnu-r28i-gpu-flash.img'))
assert (g1, g2) == (False, True), f'the gpu=1 token must ride exactly the GPU image (daily={g1}, gpu={g2})'
PY

# ---- both packed images carry the SAME kernel and the SAME untouched volume (the one-variable proof)
python3 - "$OUTDIR/raphael-xnu-r28i-flash.img" "$OUTDIR/raphael-xnu-r28i-gpu-flash.img" <<'PY'
import gzip, hashlib, struct, sys
def entries_of(path):
    img = open(path, 'rb').read()
    blob = gzip.decompress(img[4096:4096 + struct.unpack('<I', img[8:12])[0]])
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
assert a['kernel'] == b['kernel'], 'the two images must carry the SAME R28i kernel (one kernel, both images)'
assert a['ramdisk'] == b['ramdisk'], 'the two images must carry the SAME untouched volume'
assert hashlib.sha256(a['ramdisk']).hexdigest() == '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4', \
    'the volume is not the R13..R28d bytes'
print('r28i-flash-build: one kernel in both images, the R28d volume untouched in both -- the kernel entry is the only variable')
PY

echo
echo "r28i images:"
echo "  $OUTDIR/raphael-xnu-r28i-flash.img       the daily image (no GPU steps; the lsd/SIGBUS control)"
echo "  $OUTDIR/raphael-xnu-r28i-gpu-flash.img   the same + gpu=1 (step 19 runs the T2 control BY DEFAULT)"
echo "phone side (the daily control boot):"
echo "  fastboot boot  $OUTDIR/raphael-xnu-r28i-flash.img"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage raphael-r28i-flashboot.apfs   # the R28d bytes (= R13), carried verbatim"
echo "  fastboot oem  run"
echo "phone side (the T2 control, second boot):"
echo "  the same four steps with $OUTDIR/raphael-xnu-r28i-gpu-flash.img"
echo "  -> step 19 runs the T2 control with no argument needed; the verdict line is \"T2 VERDICT:\""
