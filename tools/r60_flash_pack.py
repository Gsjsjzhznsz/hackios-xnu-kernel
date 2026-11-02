#!/usr/bin/env python3
"""tools/r60_flash_pack.py -- pack the R60 session image: the R60 kernel with the JOIN cmdline.

R60 is the association-retry round (CI run 41439630833, release r60-springboard):
the R59 attempt-2 wall was `ASSOCIATION: THE ACCESS POINT REFUSES (status 53)` -- the
AP's PMKSA settling window saying "try again later"; the R60 kernel retries the
association up to 5 times with 200 ms backoff.  The CI gpu image carries that kernel
but its cmdline is the R39 form VERBATIM (the id-gate demands it) -- no wifi_join.
The join round needs the R59 cmdline token set, so this tool swaps ONLY the cmdline
entry on top of the R60 release image:

  base   = the r60 release GPU image (0a0683f1..., sha pinned in the release SHA256SUMS)
  kernel = the R60 CI build VERBATIM (entry sha e0570b2a..., 17,027,112 B -- the SAME
           byte size as the R54 kernel entry: the same-length marker swap keeps the
           segment law across generations; the CI env's KSHA 20a25ec9 names the
           pre-pack form, the ENTRY sha is the packed truth)
  loader/ramdisk/usb = the r54 lineage bytes (4bf828e4 / 5b9bbbcb), asserted
  cmdline = R39 tokens + wifi_loud=1 + wifi_scan=1 + wifi_join=1 + wifi_hold=90

Marker laws (the kernel's own vocabulary):
  - 'R60: the association asks the access point, try it again' PRESENT (the retry
    knife, same-length swap over the R54 marker slot)
  - 'R54: the decoder turns dyld fixup chains into real pages' ABSENT (the marker was
    same-length swapped to the R60 one; the R54 decode CODE still rides)
  - 'R33: the kernel generation line' x2 (the generation spine intact)

usage:
  python3 tools/r60_flash_pack.py --base raphael-xnu-r60-gpu-flash.img \
      --out-gpu raphael-xnu-r60-join.img --out-xnup r60.xnup
"""
import argparse
import gzip
import hashlib
import struct

R60_GPU = '0a0683f1e7771dc99c23446afad42c255b5001137210685a888fd3ab69f296fe'
R60_KERNEL = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'
R60_KERNEL_SIZE = 17027112
LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
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

    g_img, g_ents, geo, blob, x = unpack(args.base)
    assert sha(open(args.base, 'rb').read()) == R60_GPU, 'the base is not the r60 release GPU image'

    # ---- the base's internal laws (the r60 release = r54 lineage + the R60 kernel)
    assert sha(g_ents['loader.efi']) == LOADER, 'the loader is not the r39..r60 loader'
    assert sha(g_ents['ramdisk']) == VOLUME, 'the ramdisk is not the r39..r60 volume'
    assert tuple(cmdline_tokens(g_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'the base cmdline is not the R39 token set verbatim (the id-gate law)'
    kern = g_ents['kernel']
    assert sha(kern) == R60_KERNEL, 'the kernel is not the R60 CI build (e0570b2a)'
    assert len(kern) == R60_KERNEL_SIZE, 'the kernel is %d B, the same-length swap law pins %d' % (len(kern), R60_KERNEL_SIZE)
    assert R60_MARK in kern, 'the R60 association-retry marker is missing from the kernel'
    assert kern.count(R60_MARK) == 1, 'the R60 marker must ride exactly once'
    assert R54_MARK not in kern, 'the R54 marker still rides (the same-length swap retired it)'
    assert kern.count(R33_MARK) == 2, 'the R33 generation spine broke'
    assert LOADER_MARK in g_ents['loader.efi'], 'the loader lost the autorefresh fingerprint'
    print('base: r60 release gpu image -- loader 4bf828e4, kernel e0570b2a (R60, %d B), '
          'volume 5b9bbbcb, cmdline = R39 verbatim' % len(kern))
    print('kernel markers: R60 association-retry PRESENT x1, R54 marker retired (same-length swap), R33 spine x2')

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
    assert o_ents['kernel'] == kern, 'the packed kernel moved (must stay the R60 CI build VERBATIM)'
    assert o_ents['usb'] == g_ents['usb'] and o_ents['ramdisk'] == g_ents['ramdisk']
    assert o_ents['cmdline'] == new_cmd
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS + JOIN_TOKENS
    print('re-prove: loader/kernel/ramdisk/usb byte-identical to the r60 release; the cmdline is the join token set')

    # ---- the session xnup, extracted from the output by the same law
    blob2 = gzip.decompress(o_img[4096:4096 + struct.unpack_from('<I', o_img, 8)[0]])
    x2 = blob2.find(b'XNUP')
    p2 = blob2[x2:]
    _m, s2 = struct.unpack_from('<II', p2, 0)
    p2 = p2[:8 + s2]
    open(args.out_xnup, 'wb').write(p2)
    print('xnup: %s (%d B, sha256 %s)' % (args.out_xnup, len(p2), sha(p2)))
    assert b'wifi_join=1' in p2 and b'wifi_hold=90' in p2, 'the join tokens are not in the payload'
    assert R60_MARK in p2, 'the R60 kernel marker is not in the payload'
    print('OK: the R60 join image is packed; the retry kernel rides with the join tokens')


if __name__ == '__main__':
    main()
