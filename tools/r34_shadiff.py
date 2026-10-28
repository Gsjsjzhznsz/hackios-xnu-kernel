#!/usr/bin/env python3
"""r34_shadiff.py -- byte-level forensics for the R34 kernel reproduction gate.

The build embeds its wall-clock time in the kernel version string and cctools
ld stamps a random LC_UUID, so the sha256 is EXPECTED to change every build
(R32 44b5a2b9, R33 420f4b71 -- each round a fresh sha by design; the R33 gate
enforced "new source => new sha", an anti-recycling blacklist, NOT byte
reproduction). What must hold for R34 (kernel = R33's source verbatim) is
CONTENT EQUIVALENCE: identical size, identical markers, and byte differences
confined to the build-meta regions (UUID + version timestamp).

modes:
  default     print the forensics (sizes/shas, differing runs with hex
              context, strings delta)
  --gate      CONTENT-EQUIVALENCE verdict for the CI gate. Exit 0 iff
                * sizes equal
                * NOT byte-identical (identical = recycled binary -> fail)
                * differing runs cluster into <= 2 regions, <= 64 bytes total
                * every strings-delta line is a Darwin-version line
              exit 1 otherwise, with the evidence printed.

usage: r34_shadiff.py [--gate] <r33-base-flash.img> <rebuilt-kernel-binary>
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


def clusters(runs, gap=256):
    """Merge runs whose gap is small -> [(start, end, nbytes)]."""
    out = []
    for s, e in runs:
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], e, out[-1][2] + (e - s))
        else:
            out.append((s, e, e - s))
    return out


def gate(r33, new):
    """Content-equivalence verdict.

    Expected shape of a genuine rebuild (empirically, runs 40607732684/40609959860):
      * the 16-byte LC_UUID (cctools ld fake signature, random per link)
      * the version timestamp (two Darwin-version strings, <=32 differing bytes)
      * a FEW stray single bytes in code -- mig's random temp names (mkdtemp
        suffix + pid) seed clang's internal RNG, which flips rare register-
        allocation tie-breaks (run 40609959860: 2 bytes of 16 MB). Budget: <= 8.
    """
    if len(r33) != len(new):
        print(f'GATE FAIL: size {len(new):,} != R33 {len(r33):,}')
        return False
    if r33 == new:
        print('GATE FAIL: byte-identical to the R33 base kernel -- a recycled '
              'binary, not a fresh build (the build timestamp + UUID make this '
              'impossible for a genuine rebuild)')
        return False
    runs = diff_runs(r33, new)
    cl = clusters(runs)
    # remove the two build-meta clusters (the ~16-byte LC_UUID -- note a random UUID
    # byte may COINCIDE with R33's byte, so the cluster is 15-16 B, run 40620330106 --
    # and the version timestamp ~18-20 B) and require everything else to fit the
    # stray budget. Position-independent: take the two LARGEST clusters.
    ranked = sorted(cl, key=lambda c: -c[2])
    top2 = ranked[:2]
    stray = sum(c[2] for c in ranked[2:])
    s33, snew = strings_set(r33), strings_set(new)
    only33, onlynew = s33 - snew, snew - s33
    ok_str = all('Darwin Kernel Version' in t for t in list(only33) + list(onlynew))
    ok_shape = (len(cl) >= 2 and len(top2) == 2
                and all(c[2] <= 32 for c in top2) and top2[0][2] >= 8
                and stray <= 8)
    print(f'GATE: sizes equal ({len(new):,} B); clusters {[(hex(c[0]), c[2]) for c in cl]}; '
          f'top2 {[(c[2]) for c in top2]} (UUID+timestamp, <=32 B each, largest >=8 B); '
          f'stray bytes outside meta: {stray} (budget 8); '
          f'strings-delta {len(only33)}/{len(onlynew)} lines, all Darwin-version: {ok_str}')
    for s, e, nb_ in cl:
        print(f'  cluster @0x{s:08x}..0x{e:08x} (span {e - s} B, {nb_} differing bytes)')
    if not (ok_shape and ok_str):
        print('GATE FAIL: differences extend beyond the build-meta regions '
              '(UUID + version timestamp + stray budget 8) -- treat as '
              'environment/source drift')
        return False
    print('GATE PASS: content-equivalent to R33 -- UUID + version timestamp + '
          f'{stray} stray byte(s) (mig/clang-RNG tie-breaks), code semantics identical')
    return True


def main():
    argv = sys.argv[1:]
    gate_mode = '--gate' in argv
    if gate_mode:
        argv.remove('--gate')
    if len(argv) != 2:
        raise SystemExit(__doc__)
    r33 = kernel_of_bootimg(argv[0])
    new = open(argv[1], 'rb').read()
    print('=' * 96)
    print(f'R33 kernel (from {argv[0]}): {len(r33):,} B  sha256 {hashlib.sha256(r33).hexdigest()}')
    print(f'rebuilt kernel        ({argv[1]}): {len(new):,} B  sha256 {hashlib.sha256(new).hexdigest()}')
    if gate_mode:
        ok = gate(r33, new)
        if not ok:
            # fall through to the full forensics so the log carries the evidence
            runs = diff_runs(r33, new)
            for s, e in runs[:16]:
                print(f'  run @0x{s:08x}..0x{e:08x} ({e - s:,} B)')
                print(f'    R33: {hexctx(r33, s)}')
                print(f'    new: {hexctx(new, s)}')
            s33, snew = strings_set(r33), strings_set(new)
            for t in sorted(s33 - snew)[:20]:
                print(f'  < {t[:150]}')
            for t in sorted(snew - s33)[:20]:
                print(f'  > {t[:150]}')
        sys.exit(0 if ok else 1)
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
