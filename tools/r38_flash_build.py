#!/usr/bin/env python3
"""tools/r38_flash_build.py -- pack the r38 GPU image: the R37 bytes with THREE entries swapped.

R38 is the display + Wi-Fi round.  The kernel CHANGES for real (the first kernel round since
R33): the display round ports the reference repo's phone-proven TE scanout (three scanout
buffers at 0x9c000000, VIG0 latched at the panel's own tear effect, refresh notifications off
the measured command-mode TE -- boot-arg fb_scan=1), and the Wi-Fi stack lands whole (the
reference's WCN399x/ICNSS/MPSS/ath10k-SNOC drivers + wifi/lx/ath10k linked as the WIFI_OBJECT;
wifi_step=2 rides as the zero-side-effect dispatch verdict).  The loader also changes: it
writes /chosen "hackios,platform" (redwood_is_raphael()), enables ABL's command-mode
autorefresh on raphael (the pipeline the kernel's flips latch into), and its "oem screenshot"
reads the flipped front buffer.  The volume rides R37's bytes VERBATIM (the R37 round's
rc.jobs.r37metal containment list already in it -- this round adds no volume file).

The THREE things that move, all in the GPU image's payload:
  - the loader.efi entry: the R38 loader (v7 banner; the raphael autorefresh line is a
    fingerprint in the raw bytes)
  - the kernel entry: the R38 kernel (built this round from source; carries every R33 marker
    PLUS the R38 display/wifi strings)
  - the cmdline entry: the R37 token set + ' fb_scan=1' + ' wifi_step=2'

The daily image: R37's, byte for byte (this round's control group -- no fb_scan, no wifi_step,
no new loader, no new kernel).

Laws asserted here, from the bytes alone:
  - the base is an Android bootimg v2 whose layout obeys the truncation law
  - the base loader is the device-validated v7 (ec15106d...), the base kernel is R33
    (420f4b71...) in BOTH base images
  - the daily base carries NO gpu/adreno/metal token; the GPU base carries the full R33
    token set including adreno_keep=1 and metal=1 -- and R38 adds exactly TWO tokens
  - the base ramdisk entry is the R37 volume (7ad5b1f8...) in the GPU base, the R13 lineage
    in the daily base; the packed payload stays inside the loader's 64 MB window
  - the output re-proves every law from its own bytes

usage:
  python3 tools/r38_flash_build.py --daily base/raphael-xnu-r37-flash.img \
      --gpu base/raphael-xnu-r37-gpu-flash.img --loader xnuloader/loader.efi \
      --kernel xnu-src/.../mach.development.bcm2837 \
      --out-daily raphael-xnu-r38-flash.img --out-gpu raphael-xnu-r38-gpu-flash.img
"""
import argparse
import gzip
import hashlib
import struct
import sys

V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'   # the device-validated loader (the BASE; R38 rebuilds it)
R33 = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'  # R33's kernel; R34..R37 rode it VERBATIM
R37_VOLUME = '7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c'  # the R37 round's volume (rides VERBATIM)
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'  # the R13-lineage md1 (the DAILY images' ramdisk)
R37_GPU = '897c104c335cdccc45bd78f4db038940b484b9521c44cb68780c4fac5eb6bfa1'
R37_DAILY = 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
NEW_TOKENS = ('fb_scan=1', 'wifi_step=2')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
R38_LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
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
    """(img bytes, {entry name: bytes}, geometry) of one flash image."""
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
    ap.add_argument('--loader', required=True, help='the R38 loader.efi (xnuloader/build.sh)')
    ap.add_argument('--kernel', required=True, help='the R38 kernel')
    ap.add_argument('--out-daily', required=True)
    ap.add_argument('--out-gpu', required=True)
    args = ap.parse_args()

    loader = open(args.loader, 'rb').read()
    kernel = open(args.kernel, 'rb').read()
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the R38 loader keeps the v7 banner'
    assert R38_LOADER_MARK in loader, 'the R38 loader carries the raphael autorefresh line'
    assert kernel.count(b'R33: the kernel generation line') == 2, 'the R38 kernel keeps the R33 generation markers'
    assert b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE' in kernel, \
        'the R38 kernel carries the display round'
    assert b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45' in kernel, \
        'the R38 kernel carries the raphael Wi-Fi branch'
    print('R38 loader: %d B, sha256 %s (v7 banner + raphael autorefresh fingerprint)'
          % (len(loader), sha(loader)))
    print('R38 kernel: %d B, sha256 %s (R33 markers + display round + wifi branch)'
          % (len(kernel), sha(kernel)))

    # ---- the daily base: R37's bytes, every law, then a byte copy (the round's control group)
    d_img, d_ents, _ = unpack(args.daily)
    assert sha(open(args.daily, 'rb').read()) == R37_DAILY, 'daily: the base is not the r37 release daily'
    assert sha(d_ents['loader.efi']) == V7, 'daily: the loader is not v7'
    assert sha(d_ents['kernel']) == R33, 'daily: the kernel is not R33 (420f4b71...)'
    assert sha(d_ents['ramdisk']) == R13_VOLUME, 'daily: the base ramdisk is not the R13 lineage'
    toks = cmdline_tokens(d_ents['cmdline'])
    assert tuple(toks) == DAILY_TOKENS, 'daily: the cmdline is not the daily token set: %s' % toks
    print('daily base: the r37 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')
    open(args.out_daily, 'wb').write(d_img)

    # ---- the GPU base: R37's bytes, the full token set, and THREE entries swapped
    g_img, g_ents, geo = unpack(args.gpu)
    assert sha(open(args.gpu, 'rb').read()) == R37_GPU, 'gpu: the base is not the r37 release GPU image'
    assert sha(g_ents['loader.efi']) == V7, 'gpu: the loader is not v7'
    assert sha(g_ents['kernel']) == R33, 'gpu: the kernel is not R33'
    assert sha(g_ents['ramdisk']) == R37_VOLUME, 'gpu: the base ramdisk is not the R37 volume'
    toks = cmdline_tokens(g_ents['cmdline'])
    assert tuple(toks) == DAILY_TOKENS + GPU_TOKENS, 'gpu: the cmdline is not the R37 token set verbatim: %s' % toks
    print('gpu base: loader v7, kernel R33, volume R37, cmdline = the R37 token set verbatim')

    # the new cmdline: the same tokens, TWO additions, one trailing NUL -- as the payload carries it
    old_cmd = g_ents['cmdline'].rstrip(b'\0')
    assert b'fb_scan' not in old_cmd and b'wifi_step' not in old_cmd, 'gpu: the base already carries an R38 token'
    new_cmd = old_cmd
    for t in NEW_TOKENS:
        new_cmd += b' ' + t.encode()
    new_cmd += b'\0'
    assert tuple(cmdline_tokens(new_cmd)) == DAILY_TOKENS + GPU_TOKENS + NEW_TOKENS, \
        'the new cmdline is not exactly base + the two R38 tokens'
    print('new cmdline: %s' % new_cmd.rstrip(b'\0').decode())

    budget = sum(len(loader if n == 'loader.efi' else kernel if n == 'kernel' else new_cmd if n == 'cmdline'
                     else g_ents[n]) for n in g_ents)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('payload budget: %d B of %d B (the loader window)' % (budget, PAYLOAD_MAX))

    order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    new_ents = [[n,
                 loader if n == 'loader.efi'
                 else (kernel if n == 'kernel'
                       else (new_cmd if n == 'cmdline' else g_ents[n]))] for n in order]
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
    assert o_ents['loader.efi'] == loader and R38_LOADER_MARK in o_ents['loader.efi']
    assert o_ents['kernel'] == kernel
    assert o_ents['usb'] == g_ents['usb']
    assert sha(o_ents['ramdisk']) == R37_VOLUME, 'the packed ramdisk is not the R37 volume (it must ride verbatim)'
    assert tuple(cmdline_tokens(o_ents['cmdline'])) == DAILY_TOKENS + GPU_TOKENS + NEW_TOKENS, \
        'the packed cmdline is not the R37 set + the two R38 tokens'
    print('re-prove: ramdisk/usb byte-identical to the R37 base; loader/kernel/cmdline are the R38 three')
    print('gpu image: %s, sha256 %s' % (args.out_gpu, sha(open(args.out_gpu, 'rb').read())))
    print('daily image: %s, sha256 %s' % (args.out_daily, sha(open(args.out_daily, 'rb').read())))


if __name__ == '__main__':
    main()
