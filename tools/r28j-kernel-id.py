#!/usr/bin/env python3
"""tools/r28j-kernel-id.py -- prove WHICH kernel and WHICH loader an image carries, offline.

R28j is the T2 control, third edition -- the SEQUENTIAL round.  R28i's T2
verdict was ACCEPTED (0): the XNU->Hyp->TZ chain is sound.  But the zap's
-3145797 it answers to was measured in ANOTHER boot (R28d's, another kernel
binary), so the one comparison still missing is the two attempts in ONE boot,
back to back.  R28j makes the T2 control run BEFORE the zap attempt (no
longer instead of it): the adsp's attempt, its give-up, the zap's attempt,
its give-up -- everything between held identical; only the file, the id, and
the place it is read from differ.  Two evidence pairs ride along: the page
checks ("T2 page check" / "E1 page check", the armed lines' twins) and the
hash-segment heads ("the adsp's hash segment's head" / "the zap's hash
segment's head", the first 0x40 bytes of each -- the T3 ledger's Q5, answered
on the device).  The switch stays ON BY DEFAULT (no adreno_test_adsp token
anywhere near the cmdline), the .mdt still rides INSIDE the kernel
(raphael_adsp_fw.h), and the loader is still v7 BYTE-IDENTICAL by design
(sha ec15106d...): the kernel entry is the round's ONLY variable -- and the
daily image (no gpu=1, no GPU steps) is the lsd/SIGBUS control for the same
binary.

This unpacks any r2x flash image (R14..R28j layout: bootimg v2 -> gzip(BootShim
+ UEFI FD + XNUP payload)) and prints the kernel, loader and ramdisk entries'
sha256 plus the generation markers, so "which kernel / which loader / which
volume" is arithmetic:

    python3 tools/r28j-kernel-id.py <image> [image...]

Known kernel-entry sha256s (the rounds so far):
    R27 / R27b  372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c
    R28         9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3
    R28b        36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6
    R28c        same bytes as R28b (the kernel was unchanged; that round moved the loader)
    R28d        99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603 (run 40174469602, clean build)
    R28g        9cbcac8a4d66b1a64ec3e7b723055be367027ec190d9ef89b7c8bb0e106855a3 (run 40203429335, clean build; the lsd SIGBUS round)
    R28i        3a0d746dbc03136bd23bce1b82f6280b8bfd443b2e34ad8f93cfb3b6669fee53 (run 40234522508, clean build; the T2 ACCEPTED round)
    R28j        (this round's build; its sha is printed and enters this table next round)

Known ramdisk-entry sha256s:
    R13..R28d   372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4  (the R13 bytes; R28j rides them VERBATIM)
    R28g        870650e3... / 9308eae1...  (the rebuilt volume of the round that died; retired)

A kernel cannot print a string its binary does not carry: markers below ARE the
runtime lines, counted in the bytes that would print them.
"""
import gzip, hashlib, struct, sys

FD_SIZE = 0x300000
KNOWN = {
    '372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c': 'R27/R27b (v5-loader round)',
    '9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3': 'R28 (two-parameter baseline round)',
    '36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6': 'R28b (identity-audit round; R28c carries these same bytes by design)',
    '99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603': 'R28d (CPU-monitor waiver round; clean build, ccache 0/1036)',
    '9cbcac8a4d66b1a64ec3e7b723055be367027ec190d9ef89b7c8bb0e106855a3': 'R28g (the T2 control round; the lsd SIGBUS round, run 40203429335)',
    '3a0d746dbc03136bd23bce1b82f6280b8bfd443b2e34ad8f93cfb3b6669fee53': 'R28i (the T2 control, second edition: .mdt embedded + ON by default + the compiler pinned to theos clang v19 eabca92; the T2 ACCEPTED round, run 40234522508)',
}
KNOWN_RAMDISK = {
    '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4': 'R13..R28d (the R13 bytes; R28j rides them VERBATIM -- no rebuild this round)',
    '870650e32ee6694f18a1e8cbe7f2e1d97b131672d782e553b9428065856c9657': 'R28g (the sandbox-built rebuilt volume; retired)',
    '9308eae198d20a5d257c6dc317e1e0b3c09c3e520bcce1f2b364a63118842bbf': 'R28g (the CI-built rebuilt volume; retired)',
}

# (family, marker) -- the runtime lines, in the bytes that print them
MARKERS = [
    ('R28j generation line', b'R28j: the kernel generation line'),
    ('R28j baseline        ', b'R28j: the two-parameter baseline'),
    ('R28j T2 control      ', b'R28j: the T2 control'),
    ('R28j BEFORE round    ', b'the zap attempt follows in this same boot'),
    ('R28j zap give-up     ', b'R28j: the give-up right after the refusal'),
    ('R28j adsp give-up    ', b"R28j: the adsp's give-up"),
    ('R28j page checks     ', b'T2 page check'),
    ('R28j hash heads (ads', b"the adsp's hash segment's head"),
    ('R28j hash heads (zap', b"the zap's hash segment's head"),
    ('R28j T2 embedded copy', b"the kernel's own embedded copy"),
    ('R28j T2 armed        ', b'T2 armed'),
    ('R28j T2 verdicts     ', b'T2 VERDICT:'),
    ("R28j adsp's chain    ", b"THE ADSP'S CHAIN SAYS"),
    ('R28j fw/adsp.mbn     ', b'fw/adsp.mbn'),
    ('R28j test switch     ', b'adreno_test_adsp'),
    ('R28i generation line', b'R28i: the kernel generation line'),
    ('R28i baseline        ', b'R28i: the two-parameter baseline'),
    ('R28i T2 control      ', b'R28i: the T2 control'),
    ('R28i INSTEAD wording ', b'the step stops here by design'),
    ('R28g generation line', b'R28g: the kernel generation line'),
    ('R28g baseline        ', b'R28g: the two-parameter baseline'),
    ('R28g T2 control      ', b'R28g: the T2 control'),
    ('R28d generation line', b'R28d: the kernel generation line'),
    ('R28d baseline        ', b'R28d: the two-parameter baseline'),
    ('R28b generation line', b'R28b: the kernel generation line'),
    ('R28b baseline        ', b'R28b: the two-parameter baseline'),
    ('R28  baseline        ', b'R28: the two-parameter baseline'),
    ('R28  sweep-retire    ', b'R28: the shape sweeps, retired behind adreno_shape_sweep=1'),
    ('R27  steps-over guard', b'the steps are over (R27)'),
    ('R27  flush self-test ', b"step 1's first line: the flush channel's own test"),
    ('R26  E1 mode=2 line  ', b'R26 E1, adreno_e1_mode=2'),
    ('R25  sweep announce  ', b'R25: the shape sweeps'),
    ('R25b E7 announce     ', b'R25b: E7 moved to the end of the refusal path'),
    ('R25  E7 step-2 form  ', b'read the secboot and rollback fuses (E7: the whole qfprom windows'),
]


def unpack(path):
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
    return img, ents


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit('usage: r28j-kernel-id.py <image> [image...]')
    for path in sys.argv[1:]:
        img, ents = unpack(path)
        k = ents['kernel']
        ldr = ents.get('loader.efi', b'')
        rmd = ents.get('ramdisk', b'')
        ksha = hashlib.sha256(k).hexdigest()
        print('=' * 96)
        print(f'{path}')
        print(f'  image   sha256 : {hashlib.sha256(img).hexdigest()}')
        print(f'  kernel  sha256 : {ksha}   ({len(k):,} B)'
              + (f'   == {KNOWN[ksha]}' if ksha in KNOWN else '   == R28j (the sequential round: T2 BEFORE the zap attempt -- a NEW sha, expected this round)'))
        if rmd:
            rsha = hashlib.sha256(rmd).hexdigest()
            print(f'  ramdisk sha256 : {rsha}   ({len(rmd):,} B)'
                  + (f'   == {KNOWN_RAMDISK[rsha]}' if rsha in KNOWN_RAMDISK else ''))
        if ldr:
            lsha = hashlib.sha256(ldr).hexdigest()
            if lsha.startswith('ec15106da4bad290'):
                tag = '   == XNULOADER v7 (the R28c build, device-validated; R28j carries it verbatim)'
            elif lsha.startswith('9a93cc085b6fcd25'):
                tag = '   == XNULOADER v6 (R28 build)'
            elif lsha.startswith('5640b04be0b505fb'):
                tag = '   == XNULOADER v6 (R28b build; source-identical to R28, only the PE timestamp differs)'
            elif lsha.startswith('d42293ff2beb7653'):
                tag = '   == XNULOADER v5 (R26/R27 era)'
            else:
                tag = ''
            print(f'  loader  sha256 : {lsha}   ({len(ldr):,} B)' + tag)
            v7 = ldr.count(b'== XNULOADER v7 ==')
            keep = ldr.count(b"flush #%u's record stays")
            print(f'    loader markers: v7 banner={v7}, flush-done line={ldr.count(b"flush done, returning")}, '
                  f'handler keep-guard={keep}, v6 banner={ldr.count(b"== XNULOADER v6 ==")}')
        if 'cmdline' in ents:
            print(f'  cmdline       : {ents["cmdline"].rstrip(bytes([0])).decode(errors="replace")}')
        print('  -- kernel-entry marker counts (what this kernel CAN print) --')
        for label, m in MARKERS:
            print(f'    {label}: {k.count(m)}')
        verdict = ('R28j (the T2 control, third edition, the SEQUENTIAL round: R28j generation stamps + the '
                   'baseline + the BEFORE wordings present, the R28i INSTEAD wordings absent; the loader is the '
                   'R28c v7 verbatim, the ramdisk is the R13..R28d volume VERBATIM -- the kernel entry is the only variable)'
                   if k.count(b'R28j: the two-parameter baseline') == 1
                   and k.count(b'R28j: the T2 control') == 2
                   and k.count(b'the zap attempt follows in this same boot') == 1
                   and k.count(b'R28j: the give-up right after the refusal') == 1
                   and k.count(b"the kernel's own embedded copy") == 2
                   and k.count(b'adreno_test_adsp') == 4
                   and k.count(b'T2 VERDICT:') == 3
                   and k.count(b'the step stops here by design') == 0
                   and k.count(b'R28i: the two-parameter baseline') == 0 and k.count(b'R28i: the T2 control') == 0
                   and k.count(b'R28g: the two-parameter baseline') == 0 and k.count(b'R28g: the T2 control') == 0
                   and k.count(b'R28d: the two-parameter baseline') == 0 and k.count(b'R28d: the kernel generation line') == 0
                   and k.count(b'R28b: the two-parameter baseline') == 0 and k.count(b'R28b: the kernel generation line') == 0
                   and k.count(b'R28: the two-parameter baseline') == 0
                   else 'R28i (the T2 control, second edition; the T2 ACCEPTED round)' if k.count(b'R28i: the two-parameter baseline') == 1
                   else 'R28g (the first T2 control round; the lsd SIGBUS round)' if k.count(b'R28g: the two-parameter baseline') == 1
                   else 'R28d (CPU-monitor round)' if k.count(b'R28d: the two-parameter baseline') == 1
                   else 'R28b (identity-audit round; R28c carried these bytes)' if k.count(b'R28b: the two-parameter baseline') == 1
                   else 'R28 (two-parameter baseline round)' if k.count(b'R28: the two-parameter baseline') == 1
                   else 'R27/R27b or older (R25/R25b step-19 wordings present)'
                   if k.count(b'R25: the shape sweeps') else 'pre-R25 layout -- read the marker table')
        print(f'  VERDICT: the kernel entry is {verdict}')
        if 'cmdline' in ents:
            cmd = ents['cmdline']
            if b'adreno_test_adsp' in cmd:
                print('  NOTE: this cmdline carries an adreno_test_adsp token -- an R28g-era image, '
                      'or a hand edit; an R28j image needs NO token (the switch is the kernel default)')
            elif verdict.startswith('R28j'):
                print('  VERDICT: the cmdline needs no T2 token and carries none -- correct for R28j '
                      '(the T2 control runs on the GPU image by DEFAULT, gpu=1 alone)')
        if rmd:
            # structural: R28j's volume is the R28d one VERBATIM (no adsp.mbn in it -- the .mdt rides in the kernel)
            if hashlib.sha256(rmd).hexdigest() == '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4':
                print('  VERDICT: the ramdisk entry is R13..R28d VERBATIM (no fw/adsp.mbn -- correct for R28j: '
                      'the .mdt rides inside the kernel, the volume is never asked)')
            elif b'adsp.mbn' in rmd and b'a640_zap.mbn' in rmd:
                print('  VERDICT: the ramdisk entry is R28g (the rebuilt volume; the R28G first-second death) -- NOT this round')
            else:
                print('  VERDICT: the ramdisk entry is UNRECOGNIZED (no adsp.mbn name, not the R13 bytes either)')
    print('=' * 96)


if __name__ == '__main__':
    main()
