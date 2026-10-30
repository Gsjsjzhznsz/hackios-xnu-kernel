#!/usr/bin/env python3
"""tools/r46_flash_build.py -- pack the r46 GPU image: the R45 bytes with ONE entry swapped.

R46 is the sigbirth round.  The R45 device verdict fired ZERO shots at the
funnel -- zero sigpost lines under the recorder proven live, enabled and
sitting at bsd_exception() -- while the wave GREW to 144 deaths (lsd x108 +
analyticsd x28 + backboardd x8) and SpringBoard composed its first real
frames (flip 2/3, the white apple -- a stage R43/R44 never reached).
Silence at the funnel means nothing crossed it: no signal was ever
DELIVERED.  The one birth funnel that explains BOTH silences (usertrap AND
sigpost) is the ux_handler: the task's own exception-handler thread
translating a real fault via ux_exception() (EXC_BAD_ACCESS /
KERN_PROTECTION_FAILURE -> SIGBUS) and posting it with threadsignal(..,
TRUE).  R46 changes exactly one thing in the kernel: the recorder moves
UPSTREAM to the BIRTHS -- sigbirth_trace() in user_exception_trace.c, wired
via redwood.patch at psignal, psignal_with_reason, psignal_uthread
(pthread_kill) and threadsignal (the ux_handler's mouth), one throttled line
per signal born (target + signal + code + the unslid birth site + who is
running), a fresh (name, signal) budget and the same systrace switch; the
R44/R45 wires stay.  The marker swaps same-length.  The cmdline stays the
R39 GPU form VERBATIM -- the sigbirth needs no boot-arg.

The daily image: the r45 release's, byte for byte (this round's control
group; the r45 daily itself rode the r44/r43/r42 bytes, so the sha is the
same b0f9f6df).

Laws (all asserted here, before any packing):
  - the bases are EXACTLY the r45 release's (b0f9f6df... / aab351e3...)
  - the base loader/kernel/volume are EXACTLY the r45 release's internal
    entries (4bf828e4... / d38974bf... / 5b9bbbcb...), the loader
    fingerprint-gated
  - the new kernel carries the R33 lineage, every R38 mark, the R46 marker
    x1, the sigbirth format x1 AND the sigpost format x1 (the R45 wire
    stays), and the R45/R44/R43/R42/R41/R40 markers ZERO times, the
    "redwood-pager: verdict" literal x6 (unchanged -- R46 adds no verdict
    formats)
  - the cmdline, the md1 volume and usb are the r45 release's own bytes

usage:
  python3 tools/r46_flash_build.py --daily base/raphael-xnu-r45-flash.img \
      --gpu base/raphael-xnu-r45-gpu-flash.img \
      --kernel xnu-src/.../mach.development.bcm2837 \
      --out-daily raphael-xnu-r46-flash.img --out-gpu raphael-xnu-r46-gpu-flash.img
"""
import argparse
import gzip
import hashlib
import struct
import sys

R45_DAILY = 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b'
R45_GPU = 'aab351e3dde563603a0f85826949ce058c9030bf49b84b1f9cb73729be685aa2'
R45_LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
R45_KERNEL = 'd38974bfe3239297f0a03b4280072b4681817d3e9053f314cbf10e0f86309bd0'
R45_VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
R33_KERNEL = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
R46_MARKER = b'R46: the signal birth ledger names every hand that feeds'
R45_MARKER = b'R45: the signal funnel writes who died and what was sent'
R44_MARKER = b'R44: the kernel asks each fatal exception to name itself'
R43_MARKER = b'R43: the probe try-locks first or walks away, no recurse'
R42_MARKER = b'R42: the fault names the file that died, pager forensics'
R41_MARKER = b'R41: the snapshot buys its own ride onto the storage too'
R40_MARKER = b'R40: the first dropped frame buys the full core snapshot'
PAGER_LITERAL = b'redwood-pager: verdict'
LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
SIGPOST_FMT = b'sigpost: %s[%d] exception %d code 0x%llx 0x%llx\n'
SIGBIRTH_FMT = b'sigbirth: %s[%d] signal %d code 0x%llx from 0x%llx by %s[%d]\n'
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
    ap.add_argument('--kernel', required=True, help="this round's kernel (the R45 source + the sigbirth ledger)")
    ap.add_argument('--out-daily', required=True)
    ap.add_argument('--out-gpu', required=True)
    args = ap.parse_args()

    kernel = open(args.kernel, 'rb').read()
    assert kernel.count(b'R33: the kernel generation line') == 2, 'the R46 kernel keeps the R33 generation markers'
    assert kernel.count(b'R33: the resident round') == 1 and kernel.count(b'SCMDMP: R33 begins') == 1, \
        'the R46 kernel keeps the R33 lineage'
    assert kernel.count(R46_MARKER) == 1, 'the R46 marker must ride exactly once'
    assert kernel.count(R45_MARKER) == 0, 'the R45 marker must be retired from the R46 kernel'
    assert kernel.count(R44_MARKER) == 0, 'the R44 marker must stay retired from the R46 kernel'
    assert kernel.count(R43_MARKER) == 0, 'the R43 marker must stay retired from the R46 kernel'
    assert kernel.count(R42_MARKER) == 0, 'the R42 marker must stay retired from the R46 kernel'
    assert kernel.count(R41_MARKER) == 0, 'the R41 marker must stay retired from the R46 kernel'
    assert kernel.count(R40_MARKER) == 0, 'the R40 marker must stay retired from the R46 kernel'
    assert kernel.count(SIGBIRTH_FMT) == 1, 'the sigbirth format must ride exactly once (the birth wire packed), got %d' % kernel.count(SIGBIRTH_FMT)
    assert kernel.count(SIGPOST_FMT) == 1, 'the sigpost format must ride exactly once (the R45 funnel wire stays), got %d' % kernel.count(SIGPOST_FMT)
    assert kernel.count(PAGER_LITERAL) == 6, 'the "redwood-pager: verdict" literal must ride x6, got %d' % kernel.count(PAGER_LITERAL)
    for mark in (b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE',
                 b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45',
                 b'display: raphael command TE:',
                 b'raphael-display: flip did not latch at TE, restoring boot framebuffer',
                 b'page flips retained (fb_scan=1)'):
        assert mark in kernel, 'the R46 kernel lost an R38 mark: %s' % mark.decode()
    assert kernel.count(b'XNULIVE4') >= 1, 'the XNULIVE4 channel mark is missing'
    assert kernel.count(b'frame dropped') >= 1, 'the forensic literal "frame dropped" is missing'
    print('R46 kernel: %d B, sha256 %s (R33 lineage + R38 marks + the R46 marker x1 + the sigbirth format x1'
          ' + the sigpost format x1, R45/R44/R43/R42/R41/R40 retired, pager literal x6)'
          % (len(kernel), sha(kernel)))

    # ---- the daily base: the r45 release's bytes, every law, then a byte copy (the round's control group)
    d_img, d_ents, _ = unpack(args.daily)
    assert sha(open(args.daily, 'rb').read()) == R45_DAILY, 'daily: the base is not the r45 release daily'
    assert sha(d_ents['kernel']) == R33_KERNEL, 'daily: the kernel is not R33 (420f4b71...)'
    assert sha(d_ents['ramdisk']) == R13_VOLUME, 'daily: the base ramdisk is not the R13 lineage'
    assert tuple(cmdline_tokens(d_ents['cmdline']).split()) == DAILY_TOKENS, \
        'daily: the cmdline is not the daily token set'
    print('daily base: the r45 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')
    open(args.out_daily, 'wb').write(d_img)

    # ---- the GPU base: the r45 release's bytes; ONE entry (kernel) swapped, everything else rides
    g_img, g_ents, geo = unpack(args.gpu)
    assert sha(open(args.gpu, 'rb').read()) == R45_GPU, 'gpu: the base is not the r45 release GPU image'
    assert sha(g_ents['loader.efi']) == R45_LOADER, 'gpu: the loader is not the r45 release loader'
    assert sha(g_ents['kernel']) == R45_KERNEL, 'gpu: the kernel is not the r45 release kernel'
    assert sha(g_ents['ramdisk']) == R45_VOLUME, 'gpu: the base ramdisk is not the r39/r40 volume'
    base_cmd = g_ents['cmdline']
    assert tuple(cmdline_tokens(base_cmd).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'gpu: the cmdline is not the R39 token set verbatim'
    assert LOADER_MARK in g_ents['loader.efi'], 'the r45 loader lost the autorefresh fingerprint'
    print('gpu base: loader 4bf828e4, kernel d38974bf, volume 5b9bbbcb, cmdline = the R39 token set verbatim')
    print('new cmdline: %s (VERBATIM -- the sigbirth is ON by default, no boot-arg needed)'
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
    assert o_ents['loader.efi'] == g_ents['loader.efi'] and LOADER_MARK in o_ents['loader.efi']
    assert o_ents['kernel'] == kernel
    assert o_ents['usb'] == g_ents['usb']
    assert o_ents['ramdisk'] == g_ents['ramdisk'], 'the packed ramdisk is not the r39/r40 volume'
    assert o_ents['cmdline'] == g_ents['cmdline'], 'the packed cmdline is not verbatim'
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS
    print('re-prove: usb/DTB/loader/cmdline/ramdisk byte-identical to the r45 base; the kernel is the R46 build')
    print('gpu image: %s, sha256 %s' % (args.out_gpu, sha(open(args.out_gpu, 'rb').read())))
    print('daily image: %s, sha256 %s' % (args.out_daily, sha(open(args.out_daily, 'rb').read())))


if __name__ == '__main__':
    main()
