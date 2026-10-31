#!/usr/bin/env python3
"""tools/r51_flash_build.py -- pack the r51 GPU image: the R50 bytes with ONE entry swapped.

R51 is the prefault round.  The R50 registers re-named the hands: kr 0x2
x111 = madvise(MADV_WILLNEED) hints whose anonymous-branch prefault asks
READ|WRITE of read-only entries (x2 = 3 = MADV_WILLNEED; the R49 "syscall
buffer" reading was wrong) and kr 0x1 x30 = read-exec asks at PAC-shaped
garbage-top addresses on the exec path -- which cannot be EL1 instruction
aborts (sleh.c panics on those; this boot took none): they are
exec_prefault_data()'s non-fatal prefaults over dyld's all_image_infos
arithmetic.  R51 clamps the WILLNEED prefault to the entry's own
protection (the hint still pages the range in -- now it actually works)
and prints the failing prefault's whole sum.  The marker swaps
same-length.  The cmdline stays the R39 GPU form VERBATIM -- the hint
clamp and the prefault hologram need no boot-arg.

The daily image: the R50 release's, byte for byte (this round's control
group; the R50 daily itself rode the R49/R48/... bytes, so the sha is the
same b0f9f6df).

Laws (all asserted here, before any packing):
  - the bases are EXACTLY the r50 release's (b0f9f6df... / 0a543dc1...)
  - the base loader/kernel/volume are EXACTLY the r50 release's internal
    entries (4bf828e4... / 78c8d092... / 5b9bbbcb...), the loader
    fingerprint-gated
  - the new kernel carries the R33 lineage, every R38 mark, the R51 marker
    x1, the sigbirth format x1, the sigpost format x1 (the R46/R45 wires
    stay), the siglive format x1 AND the sigcensus births format x1 (the
    R48 census), the NEW sigcensus uf-ok tail x1 (the R51 split), the
    r49-deliver format x1 AND the redwood-fault CONTEXT format x1 (the
    R49 wires), the redwood-fault literal x2 AND the registers format x1
    (the R50 hologram, unchanged), the R51 prefault hologram format x1
    and the R51 clamp counter symbol (this round's knives), and the
    R50/R49/R48/R47/R46/R45/R44/R43/R42/R41/R40 markers ZERO times, the
    "redwood-pager: verdict" literal x6 (unchanged -- R51 adds no pager
    verdict format)
  - the cmdline, the md1 volume and usb are the r50 release's own bytes

usage:
  python3 tools/r51_flash_build.py --daily base/raphael-xnu-r50-flash.img \
      --gpu base/raphael-xnu-r50-gpu-flash.img \
      --kernel xnu-src/.../mach.development.bcm2837 \
      --out-daily raphael-xnu-r51-flash.img --out-gpu raphael-xnu-r51-gpu-flash.img
"""

import argparse
import gzip
import hashlib
import struct
import sys

R50_DAILY = 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b'
R50_GPU = '0a543dc1df47ef6c228bf15c3a907ef4a505476028af14e329a9dc1c1d90ce27'
R50_LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
R50_KERNEL = '78c8d092c2c1f8eb6c7a0e3b5348bf09e8d055ada754e4cf40759a9d99dbe362'
R50_VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
R33_KERNEL = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
R49_MARKER = b'R49: the hologram names the hand and path of every fault'
R48_MARKER = b'R48: the census counts every hand at work, live, no gate'
R50_MARKER = b'R50: the registers name the syscall behind every failure'
R51_MARKER = b'R51: the prefault names the math behind every exec fault'
R47_MARKER = b'R47: the flush rule lands every born line in the logdump'
SIGLIVE_FMT = b'siglive: birth '
SIGCENSUS_FMT = b'sigcensus: births '
SIGCENSUS2_FMT = b'sigcensus: uf-ok '
CONTEXT_FMT = b'redwood-fault: verdict %d context: '
REGISTERS_FMT = b'redwood-fault: verdict %d registers: '
EXEC_FMT = b'redwood-exec: prefault '
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
    ap.add_argument('--kernel', required=True, help="this round's kernel (the R50 source + the hint clamp + the prefault hologram)")
    ap.add_argument('--out-daily', required=True)
    ap.add_argument('--out-gpu', required=True)
    args = ap.parse_args()

    kernel = open(args.kernel, 'rb').read()
    assert kernel.count(b'R33: the kernel generation line') == 2, 'the R51 kernel keeps the R33 generation markers'
    assert kernel.count(b'R33: the resident round') == 1 and kernel.count(b'SCMDMP: R33 begins') == 1, \
        'the R51 kernel keeps the R33 lineage'
    assert kernel.count(R51_MARKER) == 1, 'the R51 marker must ride exactly once, got %d' % kernel.count(R51_MARKER)
    assert kernel.count(R50_MARKER) == 0, 'the R50 marker must stay retired from the R51 kernel'
    assert kernel.count(R49_MARKER) == 0, 'the R49 marker must stay retired from the R51 kernel'
    assert kernel.count(R48_MARKER) == 0, 'the R48 marker must stay retired from the R51 kernel'
    assert kernel.count(R47_MARKER) == 0, 'the R47 marker must stay retired from the R51 kernel'
    assert kernel.count(R46_MARKER) == 0, 'the R46 marker must stay retired from the R51 kernel'
    assert kernel.count(R45_MARKER) == 0, 'the R45 marker must stay retired from the R51 kernel'
    assert kernel.count(R44_MARKER) == 0, 'the R44 marker must stay retired from the R51 kernel'
    assert kernel.count(R43_MARKER) == 0, 'the R43 marker must stay retired from the R51 kernel'
    assert kernel.count(R42_MARKER) == 0, 'the R42 marker must stay retired from the R51 kernel'
    assert kernel.count(R41_MARKER) == 0, 'the R41 marker must stay retired from the R51 kernel'
    assert kernel.count(R40_MARKER) == 0, 'the R40 marker must stay retired from the R51 kernel'
    assert kernel.count(SIGLIVE_FMT) == 1, 'the siglive format must ride exactly once (the R48 live probe), got %d' % kernel.count(SIGLIVE_FMT)
    assert kernel.count(SIGCENSUS_FMT) == 1, 'the sigcensus births format must ride exactly once (the R48 sing-out, head line), got %d' % kernel.count(SIGCENSUS_FMT)
    assert kernel.count(SIGCENSUS2_FMT) == 1, 'the sigcensus uf-ok tail must ride exactly once (the R51 split), got %d' % kernel.count(SIGCENSUS2_FMT)
    assert kernel.count(b'redwood-fault: ') == 2, 'the redwood-fault literal must ride exactly twice (the R49 context + the R50 registers, unchanged), got %d' % kernel.count(b'redwood-fault: ')
    assert kernel.count(CONTEXT_FMT) == 1, 'the redwood-fault CONTEXT format must ride exactly once (the R49 wire), got %d' % kernel.count(CONTEXT_FMT)
    assert kernel.count(REGISTERS_FMT) == 1, 'the registers format must ride exactly once (the R50 hologram stays), got %d' % kernel.count(REGISTERS_FMT)
    assert kernel.count(b'r49-deliver: ') == 1, 'the r49-deliver format must ride exactly once (the R49 funnel wire), got %d' % kernel.count(b'r49-deliver: ')
    assert kernel.count(EXEC_FMT) == 1, 'the R51 prefault hologram format must ride exactly once, got %d' % kernel.count(EXEC_FMT)
    assert b'r51_madvise_readonly_pages' in kernel, 'the R51 kernel must carry the clamp counter symbol (the hint clamp did not link)'
    assert b'redwood_fault_seen' in kernel, 'the R51 kernel must carry the floor-census symbol'
    assert b'redwood_user_fault_exit' in kernel, 'the R51 kernel must carry the exit-census symbol'
    assert b'redwood_user_fault_deliver' in kernel, 'the R51 kernel must carry the deliver symbol'
    assert kernel.count(SIGBIRTH_FMT) == 1, 'the sigbirth format must ride exactly once (the R46 wire stays), got %d' % kernel.count(SIGBIRTH_FMT)
    assert kernel.count(SIGPOST_FMT) == 1, 'the sigpost format must ride exactly once (the R45 funnel wire stays), got %d' % kernel.count(SIGPOST_FMT)
    assert b'loader_log_flush' in kernel, 'the R51 kernel must carry the flush symbol (the R47 law rides)'
    assert kernel.count(PAGER_LITERAL) == 6, 'the "redwood-pager: verdict" literal must ride x6, got %d' % kernel.count(PAGER_LITERAL)
    for mark in (b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE',
                 b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45',
                 b'display: raphael command TE:',
                 b'raphael-display: flip did not latch at TE, restoring boot framebuffer',
                 b'page flips retained (fb_scan=1)'):
        assert mark in kernel, 'the R51 kernel lost an R38 mark: %s' % mark.decode()
    assert kernel.count(b'XNULIVE4') >= 1, 'the XNULIVE4 channel mark is missing'
    assert kernel.count(b'frame dropped') >= 1, 'the forensic literal "frame dropped" is missing'
    print('R51 kernel: %d B, sha256 %s (R33 lineage + R38 marks + the R51 marker x1 + the sigbirth format x1'
          ' + the sigpost format x1 + the siglive format x1 + the sigcensus births format x1 + the uf-ok tail x1'
          ' + the redwood-fault literal x2 (context + registers) + the r49-deliver format x1 + the prefault'
          ' hologram x1 + the clamp counter, R50/R49/R48/R47/R46/R45/R44/R43/R42/R41/R40 retired, flush symbol'
          ' packed, pager literal x6, floor-census + exit-census + deliver symbols packed)'
          % (len(kernel), sha(kernel)))

    # ---- the daily base: the r50 release's bytes, every law, then a byte copy (the round's control group)
    d_img, d_ents, _ = unpack(args.daily)
    assert sha(open(args.daily, 'rb').read()) == R50_DAILY, 'daily: the base is not the r50 release daily'
    assert sha(d_ents['kernel']) == R33_KERNEL, 'daily: the kernel is not R33 (420f4b71...)'
    assert sha(d_ents['ramdisk']) == R13_VOLUME, 'daily: the base ramdisk is not the R13 lineage'
    assert tuple(cmdline_tokens(d_ents['cmdline']).split()) == DAILY_TOKENS, \
        'daily: the cmdline is not the daily token set'
    print('daily base: the r50 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')
    open(args.out_daily, 'wb').write(d_img)

    # ---- the GPU base: the r50 release's bytes; ONE entry (kernel) swapped, everything else rides
    g_img, g_ents, geo = unpack(args.gpu)
    assert sha(open(args.gpu, 'rb').read()) == R50_GPU, 'gpu: the base is not the r50 release GPU image'
    assert sha(g_ents['loader.efi']) == R50_LOADER, 'gpu: the loader is not the r50 release loader'
    assert sha(g_ents['kernel']) == R50_KERNEL, 'gpu: the kernel is not the r50 release kernel'
    assert sha(g_ents['ramdisk']) == R50_VOLUME, 'gpu: the base ramdisk is not the r39..r51 volume'
    base_cmd = g_ents['cmdline']
    assert tuple(cmdline_tokens(base_cmd).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'gpu: the cmdline is not the R39 token set verbatim'
    assert LOADER_MARK in g_ents['loader.efi'], 'the r50 loader lost the autorefresh fingerprint'
    print('gpu base: loader 4bf828e4, kernel 78c8d092, volume 5b9bbbcb, cmdline = the R39 token set verbatim')
    print('new cmdline: %s (VERBATIM -- the hint clamp and the prefault hologram are ON by default, no boot-arg)'
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
    assert o_ents['ramdisk'] == g_ents['ramdisk'], 'the packed ramdisk is not the r39..r51 volume'
    assert o_ents['cmdline'] == g_ents['cmdline'], 'the packed cmdline is not verbatim'
    assert tuple(cmdline_tokens(o_ents['cmdline']).split()) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS
    print('re-prove: usb/DTB/loader/cmdline/ramdisk byte-identical to the r50 base; the kernel is the R51 build')
    print('gpu image: %s, sha256 %s' % (args.out_gpu, sha(open(args.out_gpu, 'rb').read())))
    print('daily image: %s, sha256 %s' % (args.out_daily, sha(open(args.out_daily, 'rb').read())))


if __name__ == '__main__':
    main()
