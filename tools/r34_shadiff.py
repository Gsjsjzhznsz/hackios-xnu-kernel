#!/usr/bin/env python3
"""r34_shadiff.py -- byte-level forensics for the R34 kernel reproduction gate.

When the rebuild's sha256 does not equal R33's published bytes (420f4b71...),
the discipline says "no flashing on suspicion": the run must stop AND say why.
This tool extracts R33's kernel entry from a pinned r33 base flash image and
compares it byte-for-byte with the freshly built kernel, so the CI log carries
the answer with it:

  * sizes and sha256 of both
  * the differing byte runs (contiguous regions, hex context around each)
  * the strings-view delta (>=10 printable chars) -- timestamps, paths and
    embedded identifiers live there

usage: r34_shadiff.py <r33-base-flash.img> <rebuilt-kernel-binary>
"""
import gzip, hashlib, struct, sys

FD_SIZE = 0x300000


def kernel_of_bootimg(path):
    """The 'kernel' entry of an XNUP-payload Android boot image (v2/4096)."""
    img = open(path, 'rb').read()
    if img[:8] != b'ANDROID!':
        raise SystemExit(f'{path}: not an Android boot image')
    k_size = struct.unpack_from('<I', img, 8)[0]
    page = struct.unpack_from('<I', img, 36)[0]
    hdr = struct.unpack_from('<I', img, 40)[0]
    if (page, hdr) != (4096, 2):
        raise SystemExit(f'{path}: not a v2/4096 image (page={page}, hdr={hdr})')
    blob = gzip.decompress(img[page:page + k_size])
    payload = blob[176 + FD_SIZE:]
    if payload[:4] != b'XNUP':
        raise SystemExit(f'{path}: no XNUP payload at shim+FD')
    size = struct.unpack_from('<I', payload, 4)[0]
    ents, p = {}, 8
    while p + 24 <= 8 + size and payload[p] != 0:
        name = payload[p:p+16].rstrip(b'\0').decode()
        esz = struct.unpack_from('<I', payload, p + 16)[0]
        ents[name] = payload[p + 24:p + 24 + esz]
        p += 24 + ((esz + 15) & ~15)
    if 'kernel' not in ents:
        raise SystemExit(f'{path}: no kernel entry (entries: {sorted(ents)})')
    return ents['kernel']


def diff_runs(a, b, cap=16):
    """Contiguous differing regions, as (start, end) half-open."""
    runs, n, minlen = [], min(len(a), len(b)), 1
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        runs.append((minlen and n, max(len(a), len(b))))
    return runs


def hexctx(blob, off, span=12):
    lo, hi = max(0, off - span), min(len(blob), off + span)
    return blob[lo:hi].hex()


def strings_set(blob, minlen=10):
    out, cur = set(), bytearray()
    for c in blob:
        if 32 <= c < 127:
            cur.append(c)
        else:
            if len(cur) >= minlen:
                out.add(cur.decode('ascii', 'replace'))
            cur = bytearray()
    if len(cur) >= minlen:
        out.add(cur.decode('ascii', 'replace'))
    return out


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    r33 = kernel_of_bootimg(sys.argv[1])
    new = open(sys.argv[2], 'rb').read()
    print('=' * 96)
    print(f'R33 kernel (from {sys.argv[1]}): {len(r33):,} B  sha256 {hashlib.sha256(r33).hexdigest()}')
    print(f'rebuilt kernel        ({sys.argv[2]}): {len(new):,} B  sha256 {hashlib.sha256(new).hexdigest()}')
    if r33 == new:
        print('IDENTICAL -- the gate disagreement is not in the kernel payload itself')
        return
    runs = diff_runs(r33, new)
    cap = 16
    total = sum(e - s for s, e in runs)
    print(f'differing byte runs: {len(runs)}   differing bytes: {total:,} '
          f'({100.0 * total / max(1, len(r33)):.4f}% of the kernel)')
    for s, e in runs[:cap]:
        print(f'  run @0x{s:08x}..0x{e:08x} ({e - s:,} B)')
        print(f'    R33: {hexctx(r33, s)}')
        print(f'    new: {hexctx(new, s)}')
    if len(runs) > cap:
        print(f'  ... and {len(runs) - cap} more runs')
        print(f'  first run @0x{runs[0][0]:08x}, last run @0x{runs[-1][0]:08x}')
    s33, snew = strings_set(r33), strings_set(new)
    only33, onlynew = sorted(s33 - snew), sorted(snew - s33)
    print(f'strings(>=10) only in R33: {len(only33)}   only in rebuilt: {len(onlynew)}')
    for t in only33[:40]:
        print(f'  < {t[:150]}')
    for t in onlynew[:40]:
        print(f'  > {t[:150]}')


if __name__ == '__main__':
    main()
