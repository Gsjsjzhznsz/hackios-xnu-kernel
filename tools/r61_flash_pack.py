#!/usr/bin/env python3
"""tools/r61_flash_pack.py -- pack the R61 session image: the R61 kernel with the JOIN cmdline.

R61 is the evidence round (CI run 41626845673, release r61-springboard): the R60
wall was `ASSOCIATION: NO ANSWER from the access point to three requests (status 53);
we are its station number 11475; its answer has 67 bytes of elements` -- the AP went
SILENT on the association requests, which points at the REQUEST construction more
than at a busy-AP window.  The R61 kernel reads the 67 answer bytes aloud in hex
(sixteen to a line), prints the honest ask count ("after %u asks", the R60 "three
requests" prose retired -- the loop is five), and announces each ask ("the
association asks, attempt N of 5").  The CI gpu image carries that kernel but its
cmdline is the R39 form VERBATIM (the id-gate demands it) -- no wifi_join.  The join
round needs the R59 cmdline token set, so this tool swaps ONLY the cmdline entry on
top of the R61 release image:

  base   = the r61 release GPU image (sha pinned in the release SHA256SUMS)
  kernel = the R61 CI build VERBATIM (entry sha pinned below from the gate report,
           17,027,112 B -- the same-length marker-swap family keeps the segment law
           across generations)
  loader/ramdisk/usb = the r54 lineage bytes (4bf828e4 / 5b9bbbcb), asserted
  cmdline = R39 tokens + wifi_loud=1 + wifi_scan=1 + wifi_join=1 + wifi_hold=90

Marker laws (the kernel's own vocabulary):
  - 'R61: the association answer read aloud in hex, all bytes' PRESENT x1
  - 'R60: the association asks the access point, try it again' ABSENT (retired by
    the same-length swap; the R60 retry CODE still rides -- five asks + backoff)
  - 'R54: the decoder turns dyld fixup chains into real pages' ABSENT (retired R60)
  - 'R33: the kernel generation line' x2 (the generation spine intact)

usage:
  python3 tools/r61_flash_pack.py --base raphael-xnu-r61-gpu-flash.img \
      --out-gpu raphael-xnu-r61-join.img --out-xnup r61.xnup
"""
import argparse
import gzip
import hashlib
import struct

R61_GPU = '39c6e70e430e424b93f6cfe40d2c7d264c3bf2dde0813087fdd48aa559f99ea1'  # PIN at release time (the release SHA256SUMS row of the gpu image)
R61_KERNEL = '8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7'  # PIN at release time (the r61-kernel-gate.txt report's new-kernel sha)
R61_KERNEL_SIZE = 17027112
LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
R61_MARK = b'R61: the association answer read aloud in hex, all bytes'
R60_MARK = b'R60: the association asks the access point, try it again'
R54_MARK = b'R54: the decoder turns dyld fixup chains into real pages'
R33_MARK = b'R33: the kernel generation line'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
JOIN_TOKENS = ('wifi_loud=1', 'wifi_scan=1', 'wifi_join=1', 'wifi_hold=90')
LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
PAYLOAD_MAX = 0x04000000


def sha(b):
    return hashlib.sha256(b).hexdigest()


def parse_payload(p):
    assert p[:4] == b'XNUP', 'bad payload magic'
    size = struct.unpack('<I', p[4:8])[0]
    body = p[8:8 + size]
    ents, i = [], 0
    while i < len(body):
        name, esize, _ = struct.unpack('<16sII', body[i:i + 24])
        if name.rstrip(b'\0') == b'':
            break
        ents.append([name.rstrip(b'\0').decode(), body[i + 24:i + 24 + esize]])
        i += 24 + esize + (-esize % 16)
    return ents


def build_payload(ents):
    body = b''
    for name, data in ents:
        body += struct.pack('<16sII', name.encode(), len(data), 0) + data + b'\0' * (-len(data) % 16)
    body += b'\0' * 24
    return struct.pack('<II', 0x50554E58, len(body)) + body


def unpack(path):
    img = open(path, 'rb').read()
    assert img[:8] == b'ANDROID!', path + ': not an Android boot image'
    page = struct.unpack_from('<I', img, 36)[0]
    assert page == 4096, path + ': page size %d' % page
    k_size = struct.unpack_from('<I', img, 8)[0]
    r_size = struct.unpack_from('<I', img, 16)[0]
    dtb_size = struct.unpack_from('<I', img, 1648)[0]
    k_off = page
    r_off = (k_off + k_size + page - 1) // page * page
    d_off = (r_off + r_size + page - 1) // page * page
    assert d_off + dtb_size == len(img), path + ': the truncation law'
    assert img[d_off:d_off + 4] == b'\xd0\x0d\xfe\xed', path + ': dtb magic'
    blob = gzip.decompress(img[k_off:k_off + k_size])
    x = blob.find(b'XNUP')
    assert x > 0, path + ': no payload'
    ents = parse_payload(blob[x:])
    assert [n for n, _ in ents] == ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb'], path + ': entry set'
    return img, dict(ents), (r_off, r_size, d_off, dtb_size), blob, x


def cmdline_tokens(data):
    return data.rstrip(b'\0').decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--out-gpu', required=True)
    ap.add_argument('--out-xnup', required=True)
    args = ap.parse_args()

    assert R61_GPU and R61_KERNEL, 'pin R61_GPU / R61_KERNEL from the r61 release first'
    g_img, g_ents, geo, blob, x = unpack(args.base)
    assert sha(open(args.base, 'rb').read()) == R61_GPU, 'the base is not the r61 release GPU image'

    # ---- the base's internal laws (the r61 release = r54 lineage + the R61 kernel)
    assert sha(g_ents['loader.efi']) == LOADER, 'the loader is not the r39..r61 loader'
    assert sha(g_ents['ramdisk']) == VOLUME, 'the ramdisk is not the r39..r61 volume'
    assert tuple(cmdline_tokens(g_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'the base cmdline is not the R39 token set verbatim (the id-gate law)'
    kern = g_ents['kernel']
    assert sha(kern) == R61_KERNEL, 'the kernel is not the R61 CI build'
    assert len(kern) == R61_KERNEL_SIZE, 'the kernel is %d B, the same-length swap law pins %d' % (len(kern), R61_KERNEL_SIZE)
    assert kern.count(R61_MARK) == 1, 'the R61 evidence marker must ride exactly once (x%d)' % kern.count(R61_MARK)
    assert R60_MARK not in kern, 'the R60 marker still rides (the same-length swap retired it)'
    assert R54_MARK not in kern, 'the R54 marker still rides (retired in R60)'
    assert kern.count(R33_MARK) == 2, 'the R33 generation spine broke'
    assert LOADER_MARK in g_ents['loader.efi'], 'the loader lost the autorefresh fingerprint'
    print('base: r61 release gpu image -- loader 4bf828e4, kernel %s (R61, %d B), '
          'volume 5b9bbbcb, cmdline = R39 verbatim' % (R61_KERNEL[:8], len(kern)))
    print('kernel markers: R61 evidence PRESENT x1, R60/R54 markers retired, R33 spine x2')

    # ---- the ONE swap: the cmdline entry
    new_cmd = (' '.join(DAILY_TOKENS + GPU_TOKENS + R39_TOKENS + JOIN_TOKENS)).encode() + b'\0'
    budget = sum(len(new_cmd if n == 'cmdline' else g_ents[n]) for n in g_ents)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('new cmdline: %s' % new_cmd.rstrip(b'\0').decode())

    order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    new_ents = [[n, new_cmd if n == 'cmdline' else g_ents[n]] for n in order]
    blob_new = blob[:x] + build_payload(new_ents)
    blob_new += b'\0' * (-len(blob_new) % 16)
    kern_gz = gzip.compress(blob_new, 6)

    out = bytearray(g_img[:4096])
    struct.pack_into('<I', out, 8, len(kern_gz))
    out += kern_gz + b'\0' * (-len(kern_gz) % 4096)
    r_off, r_size, d_off, dtb_size = geo
    out += g_img[r_off:r_off + r_size] + b'\0' * (-r_size % 4096) + g_img[d_off:d_off + dtb_size]
    open(args.out_gpu, 'wb').write(bytes(out))
    print('packed: %s (%d B, sha256 %s)' % (args.out_gpu, len(out), sha(bytes(out))))

    # ---- the output re-proves every law from its own bytes
    o_img, o_ents, _, _, _ = unpack(args.out_gpu)
    assert o_ents['loader.efi'] == g_ents['loader.efi'] and LOADER_MARK in o_ents['loader.efi']
    assert o_ents['kernel'] == kern, 'the packed kernel moved (must stay the R61 CI build VERBATIM)'
    assert o_ents['usb'] == g_ents['usb'] and o_ents['ramdisk'] == g_ents['ramdisk']
    assert o_ents['cmdline'] == new_cmd
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS + JOIN_TOKENS
    print('re-prove: loader/kernel/ramdisk/usb byte-identical to the r61 release; the cmdline is the join token set')

    # ---- the session xnup, extracted from the output by the same law
    blob2 = gzip.decompress(o_img[4096:4096 + struct.unpack_from('<I', o_img, 8)[0]])
    x2 = blob2.find(b'XNUP')
    p2 = blob2[x2:]
    _m, s2 = struct.unpack_from('<II', p2, 0)
    p2 = p2[:8 + s2]
    open(args.out_xnup, 'wb').write(p2)
    print('xnup: %s (%d B, sha256 %s)' % (args.out_xnup, len(p2), sha(p2)))
    assert b'wifi_join=1' in p2 and b'wifi_hold=90' in p2, 'the join tokens are not in the payload'
    assert R61_MARK in p2, 'the R61 kernel marker is not in the payload'
    print('OK: the R61 join image is packed; the evidence kernel rides with the join tokens')


if __name__ == '__main__':
    main()
