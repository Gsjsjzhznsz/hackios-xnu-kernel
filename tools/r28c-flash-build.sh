#!/bin/bash
# tools/r28c-flash-build.sh -- pack the r28c images for raphael (K20 Pro, hackios).
#
# R28c = the loader-fix round, on the R28b crash evidence:
#   UEFI EXCEPTION type=0 elr=a1002af8 esr=96000007 far=0 lr=a100cc44 -- the loader
#   died IN ITS OWN FLUSH REPORT: print()'s subset (v5, v6) did not know %u, printed
#   the 'u' and consumed no argument, so the arguments shifted one slot and the
#   read-back %s received the write's status word (EFI_SUCCESS, 0) as a string
#   pointer -- its first character load read address 0.  Every run since the R27
#   round died this way at its first whole-record flush (the 2-second note's, or
#   the kernel's own ask), and the exception handler then buried the record that
#   HAD landed (the crash-flush wrote 16 KB of console + kernel log, all of it
#   overwritten by the handler's console-only flush_log) before the cold reset.
#   The famous "t=4 s" was this fault, not the kernel, not E7.
#
#   The fix (xnuloader/loader.c, v7): print() knows %u; a NULL %s prints "(null)";
#   the report is followed by "flush done, returning (#N)"; a crash no longer
#   overwrites a flush record with the console alone.
#
#   The KERNEL is R28b's, byte-identical by design (sha 36a1e953... -- its stamps
#   say "R28b", correctly: it IS the R28b kernel; the round moves the loader only).
#   No binary patching anywhere: the loader is compiled from the patched source;
#   every other byte of the image is carried over from the R28b release verbatim
#   (shim, UEFI FD, kernel, ramdisk, DTB, header fields, truncation law).
#
# usage: tools/r28c-flash-build.sh <r28b-image> <r28b-gpu-image>   (release assets)
#   LOADER=...   override the v7 loader.efi (default: build from this tree)
#   OUTDIR=...   where the r28c images go (default: .)

set -e
TREE=$(cd "$(dirname "$0")/.." && pwd)
A=${1:?the R28b daily image}; B=${2:?the R28b gpu image}
OUTDIR=${OUTDIR:-$TREE}
LOADER=${LOADER:-}

# the v7 loader: compile it here unless given (clang 18 + lld-link; see xnuloader/build.sh)
if [ -z "$LOADER" ]; then
        LOADER=$TREE/xnuloader/loader.efi
        bash "$TREE/xnuloader/build.sh"
fi

python3 - "$A" "$B" "$LOADER" "$OUTDIR" <<'PY'
import gzip, hashlib, os, struct, sys

src_daily, src_gpu, loader_path, outdir = sys.argv[1:5]

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

loader = open(loader_path, 'rb').read()
assert loader.count(b'== XNULOADER v7 ==') == 1, 'the loader is not v7'
assert loader.count(b'flush done, returning') == 1, 'the flush-done line is missing'
assert loader.count(b'== XNULOADER v6 ==') == 0, 'the v6 banner is still there'

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
    old_loader = ents[0][1]
    if old_loader == loader:
        raise SystemExit(f'{src}: already v7')
    kernel_sha = sha(ents[1][1])
    assert kernel_sha == '36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6', \
        f'{src}: kernel entry is not the R28b kernel ({kernel_sha})'
    ents[0][1] = loader
    blob_new = blob[:x] + build_payload(ents)
    blob_new += b'\0' * (-len(blob_new) % 16)
    kern = gzip.compress(blob_new, 6)

    out = bytearray(img[:page])
    struct.pack_into('<I', out, 8, len(kern))
    out += kern + b'\0' * (-len(kern) % page)
    out += ramdisk + b'\0' * (-len(ramdisk) % page)
    out += dtb
    dst = os.path.join(outdir, os.path.basename(src).replace('r28b', 'r28c'))
    open(dst, 'wb').write(out)
    print(f'{dst}: {len(out)} bytes')
    print(f'  image  {sha(out)}')
    print(f'  loader {sha(loader)}  (v7, {len(loader):,} B; was {sha(old_loader)[:16]}...)')
    print(f'  kernel {kernel_sha}  (R28b, byte-identical)')
    print(f'  ramdisk/dtb: byte-identical to the R28b release image')
PY
