#!/usr/bin/env python3
"""tools/r34_flash_build.py -- pack the r34 GPU image: the R33 bytes with ONE entry swapped.

R34 is the GL-test round.  The kernel is R33's own bytes (420f4b71...) verbatim -- the
round changes NO kernel code -- and the GPU image's cmdline is R33's verbatim too
(gpu=1 adreno=22 adreno_pause=0 adreno_watch=0 adreno_keep=1).  The ONE thing that
moves is the ramdisk entry: out goes the R13-lineage md1 (372c7fcb...), in comes the
R34 GL-test volume (Mesa's libOSMesa.8.dylib + gl_test + u2f_test + etc/rc.jobs =
rc.jobs.gltest), built by tools/r34_volume_build.py from the very same R13 bytes.

The daily image: R33's, byte for byte (this round's control group).

Laws asserted here, from the bytes alone:
  - the base is an Android bootimg v2 whose layout obeys the truncation law
  - the loader entry is the device-validated v7 (ec15106d...), whole
  - the kernel entry is R33 (420f4b71...) in BOTH base images
  - the daily base carries NO gpu/adreno token; the GPU base carries the full
    R33 token set INCLUDING adreno_keep=1 -- and R34 CHANGES NO TOKEN
  - the base ramdisk entry is the R13-lineage volume (372c7fcb...), and the
    new volume's sha256 matches what r34_volume_build.py recorded
  - the packed payload (loader + kernel + volume + cmdline + usb) stays inside
    the loader's 64 MB window (PAYLOAD_BASE..+0x04000000)
  - the output re-proves every law from its own bytes

usage:
  python3 tools/r34_flash_build.py --daily base/raphael-xnu-r33-flash.img \
      --gpu base/raphael-xnu-r33-gpu-flash.img --volume r34-md1-root.apfs \
      --volume-sha <sha256 of the new volume> \
      --out-daily raphael-xnu-r34-flash.img --out-gpu raphael-xnu-r34-gpu-flash.img
"""
import argparse
import gzip
import hashlib
import os
import struct
import sys

V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'   # the device-validated loader
R33 = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'  # R33's kernel (run 40549952622); R34 rides it VERBATIM
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'  # the R13-lineage md1
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
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
    """(img bytes, {entry name: bytes}) of one flash image."""
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
    return data.rstrip(b'\0').decode().split()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--daily', required=True)
    ap.add_argument('--gpu', required=True)
    ap.add_argument('--volume', required=True)
    ap.add_argument('--volume-sha', required=True)
    ap.add_argument('--out-daily', required=True)
    ap.add_argument('--out-gpu', required=True)
    args = ap.parse_args()

    volume = open(args.volume, 'rb').read()
    got = sha(volume)
    assert got == args.volume_sha, 'the volume to embed (%s) is not the one r34_volume_build recorded (%s)' % (got, args.volume_sha)
    print('r34 volume to embed: %d B, sha256 %s' % (len(volume), got))

    # ---- the daily base: R33's bytes, every law, then a byte copy (the round's control group)
    d_img, d_ents, _ = unpack(args.daily)
    assert sha(d_ents['loader.efi']) == V7, 'daily: the loader is not v7'
    assert d_ents['loader.efi'].count(b'== XNULOADER v7 ==') == 1, 'daily: the loader banner'
    assert sha(d_ents['kernel']) == R33, 'daily: the kernel is not R33 (420f4b71...)'
    assert sha(d_ents['ramdisk']) == R13_VOLUME, 'daily: the ramdisk is not the R13 lineage'
    toks = cmdline_tokens(d_ents['cmdline'])
    assert tuple(toks) == DAILY_TOKENS, 'daily: the cmdline is not the R33 daily token set: %s' % toks
    print('daily base: loader v7, kernel R33, volume R13-lineage, no gpu/adreno token -- copying byte for byte')
    open(args.out_daily, 'wb').write(d_img)

    # ---- the GPU base: R33's bytes, the full token set, and ONE entry swapped
    g_img, g_ents, geo = unpack(args.gpu)
    assert sha(g_ents['loader.efi']) == V7, 'gpu: the loader is not v7'
    assert sha(g_ents['kernel']) == R33, 'gpu: the kernel is not R33'
    assert sha(g_ents['ramdisk']) == R13_VOLUME, 'gpu: the base ramdisk is not the R13 lineage'
    toks = cmdline_tokens(g_ents['cmdline'])
    assert tuple(toks) == DAILY_TOKENS + GPU_TOKENS, 'gpu: the cmdline is not the R33 token set verbatim: %s' % toks
    print('gpu base: loader v7, kernel R33, volume R13-lineage, cmdline = the R33 token set verbatim')

    budget = sum(len(g_ents[n]) for n in g_ents) - len(g_ents['ramdisk']) + len(volume)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('payload budget: %d B of %d B (the loader window)' % (budget, PAYLOAD_MAX))

    order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    new_ents = [[n, volume if n == 'ramdisk' else g_ents[n]] for n in order]
    blob = gzip.decompress(g_img[4096:4096 + struct.unpack_from('<I', g_img, 8)[0]])
    x = blob.find(b'XNUP')
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
    assert sha(o_ents['loader.efi']) == V7 and o_ents['loader.efi'] == g_ents['loader.efi']
    assert o_ents['kernel'] == g_ents['kernel'] and sha(o_ents['kernel']) == R33
    assert o_ents['cmdline'] == g_ents['cmdline'] and o_ents['usb'] == g_ents['usb']
    assert sha(o_ents['ramdisk']) == args.volume_sha, 'the packed ramdisk is not the R34 volume'
    print('re-prove: loader/kernel/cmdline/usb byte-identical to the R33 base; ramdisk = the R34 GL-test volume')
    print('gpu image: %s, sha256 %s' % (args.out_gpu, sha(open(args.out_gpu, 'rb').read())))
    print('daily image: %s, sha256 %s' % (args.out_daily, sha(open(args.out_daily, 'rb').read())))


if __name__ == '__main__':
    main()
