#!/bin/bash
# tools/r32-flash-build.sh -- pack the r32 images for raphael (K20 Pro, hackios).
#
# R32 = the fork-exact form, third and smallest cut, PLUS the black-screen fix.
# Two device rounds since R31: (a) R31b's u16 fix let the packed metadata REACH
# TrustZone for the first time -- still -0x300045, the wrapper code, and the QHEE
# ring finally carried the two [300045] pilot lines (pilot_init_image_to_tz /
# pilot_init_image_api_ns) while the TZ DIAG held NO sec_img_auth line (the
# auth app never ran: the refusal is the upstream shape judge); (b) the R31c
# scmsweep gate shipped with its polarity INVERTED (`sweep = 1` default), so the
# QUE read wedged the start again and the device black-screened -- R31d flips
# the default to off, and the boot chain lives on every future flash.
#
# The GPU move in R32 is a byte-level proof, not a guess: the POCO X3 Pro's
# vendor tree (same SM8150, same A640, same zap) carries the TRUE vendor pair --
# its a640_zap.elf is byte-identical to this phone's .mbn in the ELF header, the
# WHOLE phdr table and the 0x7b0 code segment, differing only inside the v6
# blob's own certificate chain; and its a640_zap.mdt equals [the .elf's header +
# phdr table VERBATIM] + [the .elf's blob @0x1000] = 6860 bytes, nothing else.
# The vendor .mdt does NOT rewrite the hash segment's p_offset -- it stays
# 0x1000, the merged stream's value, while the blob physically rides at 0x94,
# right behind the table (the v6 reader takes the metadata from behind the
# header pair, phdr[0].p_filesz).  R31's ONE edit -- rewriting 0x1000 -> 0x94
# "for self-consistency" -- moved signature-covered bytes, and the device
# refused that form exactly like the truncated page (R21) and the whole file
# (M2).  R32's builder drops the edit: header + table VERBATIM, blob moved to
# 0x94, nothing else -- the fork's own code has no p_offset logic at all.
#
# What moves in R32 (the kernel's own source, plus the image lineage):
#   zap_metadata_packed loses the p_offset rewrite (the poison); the generation
#   stamps go R31 -> R32; scm_state_dump's sweep default goes 1 -> 0 (R31d: the
#   polarity fix -- the QUE/HND/GLB/SCM/HYPDIAG regions wait for
#   the dedicated round's switch; TZREG/TZDIAG stay on the default path).
#   The M2 contract build, the N3 ring dump, the R30 sweep machinery, the T2
#   controls and the R28k run-goes-on wording all ride verbatim.
#   The surgery's BASE moves to the r31 release images: their kernel entry IS
#   the R31c kernel (05de7d6a...), their volume is the R13 lineage, their GPU
#   cmdline already carries gpu=1 + the full-step token set -- the GPU image's
#   cmdline rides VERBATIM this round (asserted, not re-appended).
#
# usage: tools/r32-flash-build.sh <r28d-image> <r28d-gpu-image>   (release assets)
#   KERNEL=...     override the R31 kernel (default: this tree's build)
#   OUTDIR=...     where the r31 images go (default: .)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R28d daily image}; B=${2:?the R28d gpu image}
OUTDIR=${OUTDIR:-$TREE}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}

[ -f "$KERNEL" ] || { echo "r32-flash-build: missing kernel $KERNEL (built by the workflow's step three -- see xnu/README.md)"; exit 1; }
echo "r31 kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"
mkdir -p "$OUTDIR"

# ---- the gate on the kernel this tree built: the round's own markers, the ones it keeps, and the ones it retires
python3 - "$KERNEL" "$TREE/fw-gpu-raphael/adsp.mbn" <<'PY'
import sys
k = open(sys.argv[1], 'rb').read()
for m, want in ((b'R32: the kernel generation line', 2),
                (b'R32: the fork-exact form', 1),
                (b'the vendor .mdt is built', 1),
                (b'p_offset rides untouched at %#x', 1),
                (b'SCMDMP: R32 begins', 1),
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
                (b"the zap's hash segment's head", 1)):
    assert k.count(m) == want, f'the built kernel: {m!r} count {k.count(m)} != {want}'
for gone in (b'R31: the kernel generation line',
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
print(f'r32-flash-build: kernel gate PASS ({len(k):,} B); R32 markers in (the fork-exact form), '
      f'the R31/R30/R28m/R28k/R28l generation stamps out, the M2 archive + N3 + R30-sweep riders in, '
      f'the adsp .mdt rides inside ({len(fw):,} B at file offset {k.find(fw):#x})')
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
R31C = '05de7d6a1228cc7e273385274cc91c96f2c23dc4cb9589c2c74197a5f2e7ed70'  # the kernel being replaced (the r31 release's build)
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'  # the R13-lineage md1 bytes

# R32: the base is the r31 release -- the GPU image's cmdline ALREADY carries
# gpu=1 and the full-step token set (adreno=22 adreno_pause=0 adreno_watch=0),
# so this round's GPU cmdline rides VERBATIM (asserted below, never re-appended;
# the announce line still prints "steps up to 22, without those in mask 0x0").
GPU_PHASE_TOKENS = ' adreno=22 adreno_pause=0 adreno_watch=0'   # (the expected set, asserted only)

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
        raise SystemExit(f'{src}: already r32')
    # the base's own laws: the loader is the device-validated v7, the kernel is the
    # R31c build (the r31 release), the volume is the R13 lineage, and the daily base
    # cmdline carries no gpu/adreno token
    assert sha(loader) == V7, f'{src}: loader entry is not the device-validated v7 ({sha(loader)[:16]}...)'
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
    assert sha(old_kernel) == R31C, f'{src}: kernel entry is not the R31c kernel being replaced ({sha(old_kernel)[:16]}...)'
    assert sha(volume) == R13_VOLUME, f'{src}: the ramdisk entry is not the R13-lineage volume ({sha(volume)[:16]}...)'
    assert b'adreno_test_adsp' not in cmdline, f'{src}: the base cmdline already carries a T2 token?!'
    old_cmdline = cmdline.rstrip(b'\0').decode()
    ents[1][1] = kernel
    # R32: the GPU image's cmdline rides VERBATIM -- the r31 base already carries
    # gpu=1 + the full-step token set (asserted, never re-appended); the daily
    # image's cmdline rides verbatim too (its adreno phase is skipped whole).
    if gpu_image:
        toks = old_cmdline.split()
        for tok in ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0'):
            assert tok in toks, f'{src}: the r31 gpu base cmdline lacks {tok}: {old_cmdline!r}'
        assert 'adreno_skip' not in old_cmdline, f'{src}: the r31 gpu base cmdline carries adreno_skip?!'
        ents[3][1] = old_cmdline.encode()
    else:
        assert 'gpu=1' not in old_cmdline.split(), f'{src}: the daily base image unexpectedly carries gpu=1?!'
        assert 'adreno=' not in old_cmdline.split(), f'{src}: the daily base image unexpectedly carries an adreno token?!'
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
        assert packed == old_cmdline, f'the gpu cmdline moved: {packed!r} (R32: it must ride VERBATIM)'
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
    print(f'  kernel  {sha(kernel)[:16]}  (R32, replacing {sha(old_kernel)[:16]}... the R31c bytes)')
    print(f'  ramdisk {sha(volume)[:16]}  (R32: the R13-lineage volume VERBATIM via R31 -- no rebuild, {len(volume):,} B)')
    if gpu_image:
        print(f'  cmdline "{packed}"  (R32: VERBATIM r31 gpu cmdline -- gpu=1 + the full-step tokens; the sweep waits for its switch)')
    else:
        print(f'  cmdline "{packed}"  (VERBATIM r31 daily; the daily image runs NO adreno step at all)')
    print(f'  dtb/outer-ramdisk: byte-identical to the r31 release image')
    return gpu

g1 = pack(src_daily, os.path.join(outdir, 'raphael-xnu-r32-flash.img'), False)
g2 = pack(src_gpu, os.path.join(outdir, 'raphael-xnu-r32-gpu-flash.img'), True)
assert (g1, g2) == (False, True), f'the gpu=1 token must ride exactly the GPU image (daily={g1}, gpu={g2})'
PY

# ---- both packed images carry the SAME kernel and the SAME untouched volume; the cmdline differs exactly by the phase tokens
python3 - "$OUTDIR/raphael-xnu-r32-flash.img" "$OUTDIR/raphael-xnu-r32-gpu-flash.img" <<'PY'
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
assert a['kernel'] == b['kernel'], 'the two images must carry the SAME R31 kernel (one kernel, both images)'
assert a['ramdisk'] == b['ramdisk'], 'the two images must carry the SAME untouched volume'
assert hashlib.sha256(a['ramdisk']).hexdigest() == '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4', \
    'the volume is not the R13..R28d bytes'
base = a['cmdline'].rstrip(b'\0').decode()
gpu = b['cmdline'].rstrip(b'\0').decode()
assert 'adreno=' not in base.split(), 'the daily image must carry NO adreno token'
assert gpu == base + ' gpu=1 adreno=22 adreno_pause=0 adreno_watch=0', \
    f'the gpu cmdline must be the daily bytes + gpu=1 + the full-step tokens, got: {gpu!r}'
print('r32-flash-build: one kernel in both images, the R28d volume untouched in both; the cmdlines differ '
      'exactly by gpu=1 + the full-step tokens -- the R28l assert stays fixed and the R28m device verdict '
      'rides as prior: the ring is wrapper-only, the recovery now reads the state around the call')
PY

echo
echo "r31 images:"
echo "  $OUTDIR/raphael-xnu-r32-flash.img       the daily image (no GPU steps; the lsd/SIGBUS control, R28k-proven clean)"
echo "  $OUTDIR/raphael-xnu-r32-gpu-flash.img   the same + gpu=1 + adreno=22 adreno_pause=0 adreno_watch=0"
echo "                                           (ALL steps -- the M2 page build, the N3 ring dump and the R31 sweep live at step 19)"
echo "phone side (the bisection round, the GPU image first):"
echo "  fastboot boot  $OUTDIR/raphael-xnu-r32-gpu-flash.img"
echo "  fastboot oem  ramdisk"
echo "  fastboot stage raphael-r31-flashboot.apfs   # the R28d bytes (= R13), carried verbatim"
echo "  fastboot oem  run"
echo "  -> expect: 'R31: the swallowed-code recovery' announce, 'steps up to 22 ... mask 0x0', then the M2"
echo "     seg table, the T2 control, the E1 attempt -- on a refusal: the HYPLOG ring dump, then the R31"
echo "     sweep region by region (SCMDMP QUE/HND/GLB, the 64 KB SCMDMP, TZDIAG, HYPDIAG), each flushed"
echo "     before the next is touched, then the give-up -- the run goes on to the 3D core (R28k)"
echo "  -> S-tree (WORKLOG preregistration): read the SCMDMP/TZDIAG regions for the true code"
echo "phone side (the control, optional): the same four steps with the daily image -- expect clean like R28k's"
