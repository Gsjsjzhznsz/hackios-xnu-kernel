#!/bin/bash
# tools/r28g-flash-build.sh -- pack the r28g images for raphael (K20 Pro, hackios).
#
# R28g = the T2 control round, on the T1 verdict (the official-package round):
#   the package IS this phone's own firmware set (hyp/tz/devcfg/storsec byte
#   for byte) and the zap the volume carries IS the stock one (the package's
#   /firmware/a640_zap.elf, sha for sha) -- so "a version mismatch" is dead
#   as a theory, and what the r28d log leaves open is the one variable it
#   never moved: another of this phone's own true firmware images over the
#   very same channel.  This round moves exactly that:
#
#     adreno_test_adsp=1  (the GPU image's cmdline) makes step 19 hand
#     TrustZone the ADSP's own .mdt -- the audio DSP's metadata image, pas id
#     1, the one image Android's own PIL starts through this very channel at
#     every boot -- instead of the zap's, over the same preemptible
#     two-parameter call, in a page of ours the same way.  Exactly two things
#     differ from the zap's own attempt: the file, and the id.  The zap's
#     whole path (the segment find, the chain verdict, E1, the place,
#     MEM_SETUP, the write, the authentication) is skipped by design, and the
#     step returns false as any refused step 19 does, so the run and the
#     system go on exactly as they do every round the zap is refused.
#
#   THE VOLUME (the only other thing that moves): fw/adsp.mbn (this tree's
#       fw-gpu-raphael/adsp.mbn = the phone's own NON-HLOS ADSP.MDT, 7,820 B,
#       sha256 f5881fc7..., "Xiaomi Attestation CA"/"Xiaomi Root CA", no
#       test-key marks) is ADDED to the md1 volume the honest way: the R28d
#       volume is read by the upstream reader (apfs.py, verified against
#       7-Zip upstream), laid out byte for byte, the file put beside the
#       three that are already there, and the volume made again by the
#       upstream builder (mkapfs.py, the same -z --plain fw the R13 volume
#       was made with) -- tools/r28g-volume-build.py, which proves on its own
#       output that every old entry is byte-identical, that the one new entry
#       is exactly the source file, that every fw file rides in one piece,
#       and that the Xiaomi chain is in and the test chain is out.
#
#   THE SURGERY on the two R28d images (the device-validated base): the
#       kernel entry is replaced by this tree's R28g build, the ramdisk entry
#       by the new volume, and the GPU image's cmdline gains
#       "adreno_test_adsp=1" (the daily image's cmdline is untouched -- the
#       test rides only where gpu=1 already rides).  The loader rides
#       VERBATIM, byte for byte (v7, ec15106d... -- the build the device has
#       already validated); the DTB, the usb entry, the header fields and the
#       truncation law are carried unchanged.  No binary patching anywhere:
#       the kernel is compiled from the patched source.
#
# usage: tools/r28g-flash-build.sh <r28d-image> <r28d-gpu-image>   (release assets)
#   KERNEL=...     override the R28g kernel (default: this tree's build)
#   OUTDIR=...     where the r28g images go (default: .)
#   APFS_TOOLS=... where apfs.py/mkapfs.py live
#                  (default: ../hackios-xnu-userland/tools/apfs; clone
#                   oosexgang/hackios-xnu-userland beside this tree)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R28d daily image}; B=${2:?the R28d gpu image}
OUTDIR=${OUTDIR:-$TREE}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}
APFS_TOOLS=${APFS_TOOLS:-$TREE/../hackios-xnu-userland/tools/apfs}

[ -f "$KERNEL" ] || { echo "r28g-flash-build: missing kernel $KERNEL (built by the workflow's step three -- see xnu/README.md)"; exit 1; }
[ -f "$TREE/fw-gpu-raphael/adsp.mbn" ] || { echo "r28g-flash-build: missing fw-gpu-raphael/adsp.mbn"; exit 1; }
echo "r28g kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"
mkdir -p "$OUTDIR"

# ---- the gate on the kernel this tree built: the round's own markers, and the ones it retires
python3 - "$KERNEL" <<'PY'
import sys
k = open(sys.argv[1], 'rb').read()
for m, want in ((b'R28g: the kernel generation line', 2),
                (b'R28g: the two-parameter baseline', 1),
                (b"the CPU monitor's override goes to every process", 1),
                (b'R28g: the T2 control', 2),
                (b'fw/adsp.mbn', 1),
                (b'adsp.mbn', 2),
                (b'adreno_test_adsp', 3),
                (b'T2 VERDICT:', 3),
                (b"THE ADSP'S CHAIN SAYS", 1),
                (b'T2 armed', 1),
                (b'the step stops here by design', 1)):
    assert k.count(m) == want, f'the built kernel: {m!r} count {k.count(m)} != {want}'
for gone in (b'R28d: the kernel generation line', b'R28d: the two-parameter baseline'):
    assert k.count(gone) == 0, f'the built kernel still carries the retired {gone!r}'
print(f'r28g-flash-build: kernel gate PASS ({len(k):,} B); the R28g markers are in, the R28d wordings are out')
PY

# ---- the base images' own md1 volume, extracted once and cross-checked between the two
BASE=$(mktemp -d)
trap 'rm -rf "$BASE"' EXIT
python3 - "$A" "$B" "$BASE/old-md1.apfs" <<'PY'
import sys
import gzip, struct
def entries_of(path):
    img = open(path, 'rb').read()
    assert img[:8] == b'ANDROID!' and struct.unpack('<I', img[36:40])[0] == 4096, 'not a v2/4096 image'
    blob = gzip.decompress(img[4096:4096 + struct.unpack('<I', img[8:12])[0]])
    x = blob.find(b'XNUP')
    assert x > 0, 'no payload'
    size = struct.unpack('<I', blob[x+4:x+8])[0]
    i, out = x + 8, {}
    while i + 24 <= x + 8 + size and blob[i] != 0:
        name = blob[i:i+16].rstrip(b'\0').decode()
        esize = struct.unpack('<I', blob[i+16:i+20])[0]
        out[name] = blob[i+24:i+24+esize]
        i += 24 + ((esize + 15) & ~15)
    assert set(out) == {'loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb'}, out.keys()
    return out
a, b = sys.argv[1:3]
ea, eb = entries_of(a), entries_of(b)
assert ea['ramdisk'] == eb['ramdisk'], 'the two R28d images do not carry the same md1 volume'
open(sys.argv[3], 'wb').write(ea['ramdisk'])
print(f'r28g-flash-build: the base volume extracted ({len(ea["ramdisk"]):,} B), identical in both images')
PY

# ---- the new volume: the R28d one + fw/adsp.mbn, built and self-verified
VOL="$OUTDIR/r28g-md1-root.apfs"
rm -f "$VOL"
python3 "$TREE/tools/r28g-volume-build.py" build "$BASE/old-md1.apfs" "$TREE/fw-gpu-raphael/adsp.mbn" "$VOL" --apfs-tools "$APFS_TOOLS"

# ---- the surgery: kernel, ramdisk, and (GPU image only) adreno_test_adsp=1
python3 - "$A" "$B" "$KERNEL" "$VOL" "$OUTDIR" <<'PY'
import gzip, hashlib, os, struct, sys

src_daily, src_gpu, kernel_path, vol_path, outdir = sys.argv[1:6]

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

kernel = open(kernel_path, 'rb').read()
volume = open(vol_path, 'rb').read()

def pack(src, out, cmdline_extra):
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
    loader, old_kernel = ents[0][1], ents[1][1]
    if old_kernel == kernel and ents[2][1] == volume:
        raise SystemExit(f'{src}: already r28g')
    # the loader rides VERBATIM: it must be the device-validated v7, byte for byte
    assert sha(loader) == V7, f'{src}: loader entry is not the device-validated v7 ({sha(loader)[:16]}...)'
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
    assert sha(old_kernel) == R28D, f'{src}: kernel entry is not the R28d kernel being replaced ({sha(old_kernel)[:16]}...)'
    old_cmdline = ents[3][1].rstrip(b'\0').decode()
    has_gpu = 'gpu=1' in old_cmdline.split()
    assert has_gpu == bool(cmdline_extra), 'the gpu=1 token must ride exactly the image the test rides'
    ents[1][1] = kernel
    ents[2][1] = volume
    if cmdline_extra:
        assert cmdline_extra not in old_cmdline.split(), 'the token is already there'
        ents[3][1] = old_cmdline.encode() + b' ' + cmdline_extra.encode() + b'\0'
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
    assert ents2[2][1] == volume, 'ramdisk identity broken'
    packed = ents2[3][1].rstrip(b'\0').decode()
    for needle in ('touchscreen=1', 'buttons=1', 'cpus=8'):
        assert needle in packed.split(), f'the packed cmdline lacks the token {needle}'
    if cmdline_extra:
        assert cmdline_extra in packed.split(), f'the packed cmdline lacks {cmdline_extra}'
    else:
        assert 'adreno_test_adsp' not in packed.split(), 'the daily image must not carry the token'
    assert 'cpus=1' not in packed.split() and '-v' not in packed.split()
    assert len(blob2[x2:]) <= (64 << 20) - 0x10000, 'payload too big for the loader window'
    data = open(out, 'rb').read()
    print(f'{out}: {len(data):,} B')
    print(f'  image   {sha(data)}')
    print(f'  loader  {sha(loader)[:16]}  (v7 VERBATIM from the R28d release, the device-validated build)')
    print(f'  kernel  {sha(kernel)[:16]}  (R28g, replacing {sha(old_kernel)[:16]}... the R28d bytes)')
    print(f'  ramdisk {sha(volume)[:16]}  (R28g: the R28d volume + fw/adsp.mbn, {len(volume):,} B)')
    print(f'  cmdline "{packed}"')
    print(f'  dtb/outer-ramdisk: byte-identical to the R28d release image')

pack(src_daily, os.path.join(outdir, 'raphael-xnu-r28g-flash.img'), '')
pack(src_gpu, os.path.join(outdir, 'raphael-xnu-r28g-gpu-flash.img'), 'adreno_test_adsp=1')
PY

# ---- the packed images re-proved to carry the new volume; the volume re-proved by the second witness
python3 - "$OUTDIR/raphael-xnu-r28g-flash.img" "$OUTDIR/raphael-xnu-r28g-gpu-flash.img" "$VOL" <<'PY'
import gzip, struct, sys
def ramdisk_of(path):
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
    return out['ramdisk']
a, b, v = (ramdisk_of(p) if p.endswith('.img') else open(p, 'rb').read() for p in sys.argv[1:4])
assert a == v and b == v, 'the packed images do not carry the new volume'
print('r28g-flash-build: both images carry the new volume, byte for byte')
PY
python3 "$TREE/tools/r24b_volume_audit.py" gate "$VOL" | tail -2
python3 "$TREE/tools/r28g-volume-build.py" verify "$BASE/old-md1.apfs" "$VOL" "$TREE/fw-gpu-raphael/adsp.mbn" --apfs-tools "$APFS_TOOLS" | tail -9

echo
echo "r28g images and volume:"
echo "  $OUTDIR/raphael-xnu-r28g-flash.img       the daily image (no GPU steps; cmdline untouched)"
echo "  $OUTDIR/raphael-xnu-r28g-gpu-flash.img   the same + gpu=1 adreno_test_adsp=1 (the T2 control)"
echo "  $OUTDIR/r28g-md1-root.apfs               the new md1 volume (the R28d one + fw/adsp.mbn)"
echo "phone side (the daily boot, unchanged):"
echo "  fastboot boot  $OUTDIR/raphael-xnu-r28g-flash.img"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage raphael-r28g-flashboot.apfs   # the R13 bytes, carried verbatim"
echo "  fastboot oem  run"
echo "phone side (the T2 control, second boot):"
echo "  the same four steps with $OUTDIR/raphael-xnu-r28g-gpu-flash.img"
echo "  -> step 19 hands TrustZone the ADSP's own .mdt instead of the zap's;"
echo "     the verdict line is \"T2 VERDICT:\" -- see the round's README for the tree"
