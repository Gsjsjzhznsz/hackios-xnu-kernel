#!/bin/bash
# tools/r28d-flash-build.sh -- pack the r28d images for raphael (K20 Pro, hackios).
#
# R28d = the CPU-monitor round, on the R28c field report (r28c-logdump):
#   lsd[29] was spun from 62.49 s to the run's end at 73.63 s -- 144 denials of
#   "process lsd[29] denied attempt to escape CPU monitor (missing required
#   entitlement)" -- while backboardd kept rendering the white apple and
#   SpringBoard, waiting on lsd for its app registrations, never came up.
#   The chain, read out of the sources: launchd arms a CPU monitor at lsd's
#   spawn (posix_spawnattr's cpumonitor percent, kern_exec.c treats it as
#   entitled); lsd later asks to clear it by the process_policy call; the
#   kernel answers from the code signature's entitlement list
#   (com.apple.private.kernel.override-cpumon, our privilege 1015), does not
#   find the key, and -- the punchline -- the refusal does not fail the call:
#   task_policy.c substitutes the defaults, clamps them to the global caps,
#   RE-ARMS the monitor, and answers 0, so the caller cannot see the refusal
#   and retries for as long as it runs.  The round before stood at the same
#   wall unreported: its capture ended at 48 s with lsd 0.7 s old, short of
#   the 2.4 s at which the wall shows.
#   The fix (xnu/files/bsd/kern/kern_minimac.c): PRIV_PROC_CPUMON_OVERRIDE
#   joins PRIV_WORK_INTERVAL in the granted-to-everyone tier -- the same
#   file's own precedent for a refusal that is fatal, now with its own
#   first-time line ("the refusal was fatal to lsd -- R28d").
#
#   The LOADER is R28c's, BYTE-IDENTICAL by design (v7, sha ec15106d... -- the
#   build the device has already validated this round; its stamps still say
#   v7, correctly: the round moves the kernel only).  No binary patching
#   anywhere: the kernel is compiled from the patched source; every other
#   byte of the image is carried over from the R28c release verbatim (loader,
#   ramdisk, DTB, header fields, truncation law).
#
# usage: tools/r28d-flash-build.sh <r28c-image> <r28c-gpu-image>   (release assets)
#   KERNEL=...   override the R28d kernel (default: this tree's build)
#   OUTDIR=...   where the r28d images go (default: .)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R28c daily image}; B=${2:?the R28c gpu image}
OUTDIR=${OUTDIR:-$TREE}
KERNEL=${KERNEL:-$TREE/xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837}

[ -f "$KERNEL" ] || { echo "r28d-flash-build: missing kernel $KERNEL (built by the workflow's step three -- see xnu/README.md)"; exit 1; }
echo "r28d kernel (built from this tree): $KERNEL ($(stat -c %s "$KERNEL") B, $(stat -c %y "$KERNEL" | cut -d. -f1))"

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
R28B = '36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6'  # the kernel being replaced

kernel = open(kernel_path, 'rb').read()
# what this kernel CAN print -- the counts the round is gated on
for m, want in ((b'R28d: the kernel generation line', 2),
                (b'R28d: the two-parameter baseline', 1),
                (b"the CPU monitor's override goes to every process", 1)):
    assert kernel.count(m) == want, f'the built kernel: {m!r} count {kernel.count(m)} != {want}'
for m in (b'R28b: the kernel generation line', b'R28b: the two-parameter baseline'):
    assert kernel.count(m) == 0, f'the built kernel still carries the retired {m!r}'

for src in (src_daily, src_gpu):
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
    loader = ents[0][1]
    old_kernel = ents[1][1]
    if old_kernel == kernel:
        raise SystemExit(f'{src}: already r28d')
    # the loader rides VERBATIM: it must be the device-validated v7, byte for byte
    assert sha(loader) == V7, f'{src}: loader entry is not the device-validated v7 ({sha(loader)[:16]}...)'
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
    assert loader.count(b'flush done, returning') == 1, 'the flush-done line is missing'
    assert loader.count(b'== XNULOADER v6 ==') == 0, 'the v6 banner is still there'
    assert sha(old_kernel) == R28B, f'{src}: kernel entry is not the R28b kernel being replaced ({sha(old_kernel)[:16]}...)'
    ents[1][1] = kernel
    blob_new = blob[:x] + build_payload(ents)
    blob_new += b'\0' * (-len(blob_new) % 16)
    kern = gzip.compress(blob_new, 6)

    out = bytearray(img[:page])
    struct.pack_into('<I', out, 8, len(kern))
    out += kern + b'\0' * (-len(kern) % page)
    out += ramdisk + b'\0' * (-len(ramdisk) % page)
    out += dtb
    dst = os.path.join(outdir, os.path.basename(src).replace('r28c', 'r28d'))
    open(dst, 'wb').write(out)
    print(f'{dst}: {len(out)} bytes')
    print(f'  image  {sha(out)}')
    print(f'  loader {sha(loader)}  (v7 VERBATIM from the R28c release, the device-validated build)')
    print(f'  kernel {sha(kernel)}  (R28d, replacing {sha(old_kernel)[:16]}... the R28b bytes)')
    print(f'  ramdisk/dtb: byte-identical to the R28c release image')
PY
