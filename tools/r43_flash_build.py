#!/usr/bin/env python3
"""tools/r43_flash_build.py -- pack the r43 GPU image: the R42 bytes with ONE entry swapped.

R43 is the try-lock probe round.  The R42 device verdict (5600 logdump lines on the
record) proved the forensics chain alive (160 verdicts, throttle + flush worked) and
killed two assumptions: lsd's SIGBUS loop never passes vm_fault_internal's failure
exit (page-fill hypothesis refuted -- R44's lead is the R28d sandbox-refusal note),
and the probe's own shadow-chain loop took one object's exclusive lck_rw twice
(lock_rw.c:877, owner == self, SpringBoard panicked -- the black screen).  R43
changes exactly one thing in the kernel: the probe file
(files/osfmk/vm/redwood_fault_convict.c) now try-locks everything (vm_map_try_
lock_read, vm_object_lock_try_scan), locks each chain level exactly once, and
prints a minimal busy verdict instead of recursing; two NEW busy formats join the
four verdict formats (pager literal x6), and the marker swaps same-length.

The daily image: the r42 release's, byte for byte (this round's control group; the
r42 daily itself rode the r41 bytes, so the sha is the same b0f9f6df).

Laws (all asserted here, before any packing):
  - the bases are EXACTLY the r42 release's (b0f9f6df... / 055a3cb1...)
  - the base loader/kernel/volume are EXACTLY the r42 release's internal entries
    (4bf828e4... / ef204f28... / 5b9bbbcb...), the loader fingerprint-gated
  - the new kernel carries the R33 lineage, every R38 mark, the R43 marker x1 and
    the R42/R41/R40 markers ZERO times, the "redwood-pager: verdict" literal x6
  - the cmdline, the md1 volume and usb are the r42 release's own bytes

usage:
  python3 tools/r43_flash_build.py --daily base/raphael-xnu-r42-flash.img \
      --gpu base/raphael-xnu-r42-gpu-flash.img \
      --kernel xnu-src/.../mach.development.bcm2837 \
      --out-daily raphael-xnu-r43-flash.img --out-gpu raphael-xnu-r43-gpu-flash.img
"""
import argparse
import gzip
import hashlib
import struct
import sys

R42_DAILY = 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b'
R42_GPU = '055a3cb14ed670c9574f06440dc69f48876e97c33603dee8c6ed4fff4969eb4a'
R42_LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
R42_KERNEL = 'ef204f284b5825d3598d7b2630159945dbb11e990f0eac75bcbff30f3f15c021'
R42_VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
R33_KERNEL = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
R43_MARKER = b'R43: the probe try-locks first or walks away, no recurse'
R42_MARKER = b'R42: the fault names the file that died, pager forensics'
R41_MARKER = b'R41: the snapshot buys its own ride onto the storage too'
R40_MARKER = b'R40: the first dropped frame buys the full core snapshot'
PAGER_LITERAL = b'redwood-pager: verdict'
R42_LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
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
    return data.rstrip(b'\0').decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--daily', required=True)
    ap.add_argument('--gpu', required=True)
    ap.add_argument('--kernel', required=True, help="this round's kernel (the R42 source + the try-lock fix)")
    ap.add_argument('--out-daily', required=True)
    ap.add_argument('--out-gpu', required=True)
    args = ap.parse_args()

    kernel = open(args.kernel, 'rb').read()
    assert kernel.count(b'R33: the kernel generation line') == 2, 'the R43 kernel keeps the R33 generation markers'
    assert kernel.count(b'R33: the resident round') == 1 and kernel.count(b'SCMDMP: R33 begins') == 1, \
        'the R43 kernel keeps the R33 lineage'
    assert kernel.count(R43_MARKER) == 1, 'the R43 marker must ride exactly once'
    assert kernel.count(R42_MARKER) == 0, 'the R42 marker must be retired from the R43 kernel'
    assert kernel.count(R41_MARKER) == 0, 'the R41 marker must stay retired from the R43 kernel'
    assert kernel.count(R40_MARKER) == 0, 'the R40 marker must stay retired from the R43 kernel'
    assert kernel.count(PAGER_LITERAL) == 6, 'the "redwood-pager: verdict" literal must ride x6, got %d' % kernel.count(PAGER_LITERAL)
    for mark in (b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE',
                 b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45',
                 b'display: raphael command TE:',
                 b'raphael-display: flip did not latch at TE, restoring boot framebuffer',
                 b'page flips retained (fb_scan=1)'):
        assert mark in kernel, 'the R43 kernel lost an R38 mark: %s' % mark.decode()
    assert kernel.count(b'XNULIVE4') >= 1, 'the XNULIVE4 channel mark is missing'
    assert kernel.count(b'frame dropped') >= 1, 'the forensic literal "frame dropped" is missing'
    print('R43 kernel: %d B, sha256 %s (R33 lineage + R38 marks + the R43 marker x1, R42/R41/R40 retired, pager literal x6)'
          % (len(kernel), sha(kernel)))

    # ---- the daily base: the r42 release's bytes, every law, then a byte copy (the round's control group)
    d_img, d_ents, _ = unpack(args.daily)
    assert sha(open(args.daily, 'rb').read()) == R42_DAILY, 'daily: the base is not the r42 release daily'
    assert sha(d_ents['kernel']) == R33_KERNEL, 'daily: the kernel is not R33 (420f4b71...)'
    assert sha(d_ents['ramdisk']) == R13_VOLUME, 'daily: the base ramdisk is not the R13 lineage'
    assert tuple(cmdline_tokens(d_ents['cmdline']).split()) == DAILY_TOKENS, \
        'daily: the cmdline is not the daily token set'
    print('daily base: the r42 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')
    open(args.out_daily, 'wb').write(d_img)

    # ---- the GPU base: the r42 release's bytes; ONE entry (kernel) swapped, everything else rides
    g_img, g_ents, geo = unpack(args.gpu)
    assert sha(open(args.gpu, 'rb').read()) == R42_GPU, 'gpu: the base is not the r42 release GPU image'
    assert sha(g_ents['loader.efi']) == R42_LOADER, 'gpu: the loader is not the r42 release loader'
    assert sha(g_ents['kernel']) == R42_KERNEL, 'gpu: the kernel is not the r42 release kernel'
    assert sha(g_ents['ramdisk']) == R42_VOLUME, 'gpu: the base ramdisk is not the r39/r40 volume'
    base_cmd = g_ents['cmdline']
    assert tuple(cmdline_tokens(base_cmd).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'gpu: the cmdline is not the R39 token set verbatim'
    assert R42_LOADER_MARK in g_ents['loader.efi'], 'the r42 loader lost the autorefresh fingerprint'
    print('gpu base: loader 4bf828e4, kernel ef204f28, volume 5b9bbbcb, cmdline = the R39 token set verbatim')
    print('new cmdline: %s (VERBATIM -- no churn once the firmware travels in the flashboot)'
          % cmdline_tokens(base_cmd).rstrip('\0'))

    budget = sum(len(kernel if n == 'kernel' else g_ents[n]) for n in g_ents)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('payload budget: %d B of %d B (the loader window)' % (budget, PAYLOAD_MAX))

    order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    new_ents = [[n, kernel if n == 'kernel' else g_ents[n]] for n in order]
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
    assert o_ents['loader.efi'] == g_ents['loader.efi'] and R42_LOADER_MARK in o_ents['loader.efi']
    assert o_ents['kernel'] == kernel
    assert o_ents['usb'] == g_ents['usb']
    assert o_ents['ramdisk'] == g_ents['ramdisk'], 'the packed ramdisk is not the r39/r40 volume'
    assert o_ents['cmdline'] == g_ents['cmdline'], 'the packed cmdline is not verbatim'
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS
    print('re-prove: usb/DTB/loader/cmdline/ramdisk byte-identical to the r42 base; the kernel is the R43 build')
    print('gpu image: %s, sha256 %s' % (args.out_gpu, sha(open(args.out_gpu, 'rb').read())))
    print('daily image: %s, sha256 %s' % (args.out_daily, sha(open(args.out_daily, 'rb').read())))


if __name__ == '__main__':
    main()
