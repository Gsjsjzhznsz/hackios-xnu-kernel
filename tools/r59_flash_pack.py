#!/usr/bin/env python3
"""tools/r59_flash_pack.py -- pack the r59 GPU image: the R54 bytes with the CMDLINE entry swapped.

R59 is the join round (the plan's R37 row, second half).  R58's scan heard 8 networks
(all RSN, none open); the ONLY missing input is the credential pair, which rides in
fw/network of the staged flashboot -- NOT on the cmdline (redwood_wifi.c: "Not a boot
argument: those are echoed by the loader").  This round's knife is therefore one
cmdline token: wifi_join=1, which routes lx_bus.c's bring-up into lx_station_join()
(join wins over scan; the R58 wifi_scan=1 stays as the fallback shape).  The kernel is
the R54 build VERBATIM (KSHA 36438a97 -- zero kernel change since R54, four rounds of
evidence).  The observation knobs ride with it, declared: wifi_loud=1 (R57, the kept-ring
bypass) and wifi_hold=90 (after a successful join lx_station_say() prints the frame
counters after 90 s -- the R58 '0 frames sent/received' shape becomes a real count).

Laws (all asserted here, before any packing):
  - the base is EXACTLY the r54 release GPU image (37b0ee8a...)
  - the base loader/kernel/ramdisk/usb are EXACTLY the r54 release's internal entries
    (4bf828e4... / 36438a97... / 5b9bbbcb...), the loader fingerprint-gated
  - the base cmdline is the R39 token set verbatim (DAILY + GPU + R39, nothing else)
  - the new cmdline = the R39 set + wifi_loud=1 + wifi_scan=1 + wifi_join=1 + wifi_hold=90
  - the payload budget fits the loader's 64 MB window
  - the output re-proves every law from its own bytes (loader/kernel/ramdisk/usb
    byte-identical to the base; the cmdline is the new token set)
  - the r54 XNUP extraction law re-proves FIRST: extracting the base's payload must
    reproduce the recorded r54.xnup sha (30431a90 prefix) and size

usage:
  python3 tools/r59_flash_pack.py --base raphael-xnu-r54-gpu-flash.img \
      --out-gpu raphael-xnu-r59-gpu-flash.img --out-xnup r59.xnup
"""
import argparse
import gzip
import hashlib
import struct
import sys

R54_GPU = '37b0ee8aa089b89a99603a32a977c97ab364e99227a37333714b4e310b8eed92'
# the ledger pins these by their 8-hex prefixes (docs/WORKLOG.md R54/R57 entries):
#   kernel 36438a97 (17,027,112 B), r54.xnup 30431a90, loader 4bf828e4, volume 5b9bbbcb
R54_KERNEL_PREFIX = '36438a97'
R54_KERNEL_SIZE = 17027112
R54_XNUP_PREFIX = '30431a90'
LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
NEW_TOKENS = ('wifi_loud=1', 'wifi_scan=1', 'wifi_join=1', 'wifi_hold=90')
LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
PAYLOAD_MAX = 0x04000000   # the loader's window: PAYLOAD_BASE..+64 MB


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
    return img, dict(ents), (r_off, r_size, d_off, dtb_size)


def cmdline_tokens(data):
    return data.rstrip(b'\0').decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--out-gpu', required=True)
    ap.add_argument('--out-xnup', required=True)
    args = ap.parse_args()

    # ---- the extraction law first: the base's payload must re-derive the recorded r54.xnup
    g_img, g_ents, geo = unpack(args.base)
    assert sha(open(args.base, 'rb').read()) == R54_GPU, 'gpu: the base is not the r54 release GPU image'
    blob = gzip.decompress(g_img[4096:4096 + struct.unpack_from('<I', g_img, 8)[0]])
    x = blob.find(b'XNUP')
    payload = blob[x:]
    magic, size = struct.unpack_from('<II', payload, 0)
    assert magic == 0x50554E58 and 8 + size <= len(payload)
    payload = payload[:8 + size]
    got = sha(payload)
    assert got.startswith(R54_XNUP_PREFIX), 'the extraction law FAILED: base payload %s != recorded r54.xnup %s...' % (got, R54_XNUP_PREFIX)
    print('extraction law: base payload = the recorded r54.xnup (%s...), %d B, sha256 %s' % (R54_XNUP_PREFIX, len(payload), got))

    # ---- the base's internal laws
    assert sha(g_ents['loader.efi']) == LOADER, 'gpu: the loader is not the r39..r54 loader'
    assert sha(g_ents['kernel']).startswith(R54_KERNEL_PREFIX), 'gpu: the kernel is not the R54 build (36438a97)'
    assert len(g_ents['kernel']) == R54_KERNEL_SIZE, 'gpu: the kernel is %d B, the R54 ledger pins %d' % (len(g_ents['kernel']), R54_KERNEL_SIZE)
    assert sha(g_ents['ramdisk']) == VOLUME, 'gpu: the ramdisk is not the r39..r54 volume'
    base_cmd = g_ents['cmdline']
    assert tuple(cmdline_tokens(base_cmd).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'gpu: the base cmdline is not the R39 token set verbatim'
    assert LOADER_MARK in g_ents['loader.efi'], 'the loader lost the autorefresh fingerprint'
    print('base: loader 4bf828e4, kernel 36438a97 (R54, VERBATIM), volume 5b9bbbcb, cmdline = the R39 token set verbatim')

    # ---- the ONE swap: the cmdline entry
    new_cmd = (' '.join(DAILY_TOKENS + GPU_TOKENS + R39_TOKENS + NEW_TOKENS)).encode() + b'\0'
    budget = sum(len(new_cmd if n == 'cmdline' else g_ents[n]) for n in g_ents)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('new cmdline: %s' % new_cmd.rstrip(b'\0').decode())
    print('payload budget: %d B of %d B' % (budget, PAYLOAD_MAX))

    order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    new_ents = [[n, new_cmd if n == 'cmdline' else g_ents[n]] for n in order]
    blob_new = blob[:x] + build_payload(new_ents)
    blob_new += b'\0' * (-len(blob_new) % 16)
    kern = gzip.compress(blob_new, 6)

    out = bytearray(g_img[:4096])
    struct.pack_into('<I', out, 8, len(kern))
    out += kern + b'\0' * (-len(kern) % 4096)
    r_off, r_size, d_off, dtb_size = geo
    out += g_img[r_off:r_off + r_size] + b'\0' * (-r_size % 4096) + g_img[d_off:d_off + dtb_size]
    open(args.out_gpu, 'wb').write(bytes(out))
    print('packed: %s (%d B)' % (args.out_gpu, len(out)))

    # ---- the output re-proves every law from its own bytes
    o_img, o_ents, _ = unpack(args.out_gpu)
    assert o_ents['loader.efi'] == g_ents['loader.efi'] and LOADER_MARK in o_ents['loader.efi']
    assert o_ents['kernel'] == g_ents['kernel'], 'the packed kernel moved (must stay 36438a97 VERBATIM)'
    assert o_ents['usb'] == g_ents['usb']
    assert o_ents['ramdisk'] == g_ents['ramdisk']
    assert o_ents['cmdline'] == new_cmd, 'the packed cmdline is not the new token set'
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS + NEW_TOKENS
    print('re-prove: loader/kernel/ramdisk/usb byte-identical to the r54 base; the cmdline is the R59 token set')

    # ---- the round's xnup, extracted from the output by the same law
    blob2 = gzip.decompress(o_img[4096:4096 + struct.unpack_from('<I', o_img, 8)[0]])
    x2 = blob2.find(b'XNUP')
    p2 = blob2[x2:]
    _m, s2 = struct.unpack_from('<II', p2, 0)
    p2 = p2[:8 + s2]
    open(args.out_xnup, 'wb').write(p2)
    print('xnup: %s (%d B, sha256 %s)' % (args.out_xnup, len(p2), sha(p2)))
    assert b'wifi_join=1' in p2 and b'wifi_hold=90' in p2, 'the join tokens are not in the payload'
    print('OK: the r59 image is packed; the join tokens ride inside the payload cmdline entry')


if __name__ == '__main__':
    main()
