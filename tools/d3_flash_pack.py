#!/usr/bin/env python3
"""Pack a pinned D3 release with the D0b session cmdline.

Usage: --base PATH --base-sha SHA --kernel-sha SHA --out-gpu PATH --out-xnup PATH
"""
import sys
if not __debug__:
    sys.exit("D3 checks require Python without -O")

import argparse
import gzip
import hashlib
import struct

LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
D3_MARK = b'D3: the 3D core guard covers two megabytes for diagnosis'
OLD_MARK = b'D1: the stalled command buffers read aloud, word by word'
R61_MARK = b'R61: the association answer read aloud in hex, all bytes'
R33_MARK = b'R33: the kernel generation line'
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
GPU_TOKENS = ('gpu=1', 'fb_scan=1', 'wifi=0')      # the D0b display shape: the adreno/metal five GONE, wifi explicit-off
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
    ap.add_argument('--base-sha', required=True, help='the d3 release GPU image sha256 (pinned at download time)')
    ap.add_argument('--kernel-sha', required=True, help='the D3 CI kernel sha256 (from the gate report)')
    ap.add_argument('--out-gpu', required=True)
    ap.add_argument('--out-xnup', required=True)
    args = ap.parse_args()

    g_img, g_ents, geo, blob, x = unpack(args.base)
    assert sha(open(args.base, 'rb').read()) == args.base_sha, 'the base is not the pinned d3 release GPU image'

    # ---- the base's internal laws (the d3 release = r54..r61 lineage + the D3 kernel)
    assert sha(g_ents['loader.efi']) == LOADER, 'the loader is not the r39..r61 loader'
    assert sha(g_ents['ramdisk']) == VOLUME, 'the ramdisk is not the r39..r61 volume'
    R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
    GPU_TOKENS_R39 = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
    assert tuple(cmdline_tokens(g_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS_R39 + R39_TOKENS, \
        'the base cmdline is not the R39 token set verbatim (the id-gate law)'
    kern = g_ents['kernel']
    assert sha(kern) == args.kernel_sha, 'the kernel is not the pinned D3 CI build'
    assert kern.count(D3_MARK) == 1, 'the D3 marker must ride exactly once (x%d)' % kern.count(D3_MARK)
    assert OLD_MARK not in kern, 'the D1 marker must be retired'
    assert R61_MARK not in kern, 'the R61 marker still rides (the same-length swap retired it)'
    assert kern.count(R33_MARK) == 2, 'the R33 generation spine broke'
    assert b'adreno: gpu: dump:' in kern, 'the D1 dump prints are not in the kernel'
    assert LOADER_MARK in g_ents['loader.efi'], 'the loader lost the autorefresh fingerprint'
    print('base: d3 release gpu image -- loader 4bf828e4, kernel %s (D3, %d B), '
          'volume 5b9bbbcb, cmdline = R39 verbatim' % (args.kernel_sha[:8], len(kern)))
    print('kernel markers: D3 PRESENT x1, R61 retired, R33 spine x2; the dump prints ride')

    # ---- the ONE swap: the cmdline entry
    new_cmd = (' '.join(DAILY_TOKENS + GPU_TOKENS)).encode() + b'\0'
    budget = sum(len(new_cmd if n == 'cmdline' else g_ents[n]) for n in g_ents)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('new cmdline: %s (%d B -- the D0b display shape)' % (new_cmd.rstrip(b'\0').decode(), len(new_cmd)))

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
    assert o_ents['kernel'] == kern, 'the packed kernel moved (must stay the D3 CI build VERBATIM)'
    assert o_ents['usb'] == g_ents['usb'] and o_ents['ramdisk'] == g_ents['ramdisk']
    assert o_ents['cmdline'] == new_cmd
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS
    print('re-prove: loader/kernel/ramdisk/usb byte-identical to the d3 release; the cmdline is the D0b display shape')

    # ---- the session xnup, extracted from the output by the same law
    blob2 = gzip.decompress(o_img[4096:4096 + struct.unpack_from('<I', o_img, 8)[0]])
    x2 = blob2.find(b'XNUP')
    p2 = blob2[x2:]
    _m, s2 = struct.unpack_from('<II', p2, 0)
    p2 = p2[:8 + s2]
    open(args.out_xnup, 'wb').write(p2)
    print('xnup: %s (%d B, sha256 %s)' % (args.out_xnup, len(p2), sha(p2)))
    packed_cmd = dict(parse_payload(p2))['cmdline']
    assert tuple(cmdline_tokens(packed_cmd).split()) == DAILY_TOKENS + GPU_TOKENS, 'the session cmdline does not match the D0b display shape'
    assert D3_MARK in p2, 'the D3 kernel marker is not in the payload'
    print('OK: the D3 forensic image is packed; the stall forensics kernel rides with the D0b display shape')


if __name__ == '__main__':
    main()
