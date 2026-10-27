#!/usr/bin/env python3
"""tools/r31-kernel-id.py -- prove WHICH kernel and WHICH loader an image carries, offline.

R31 is the fork-proven-form round.  The HeliumStudio port of THIS VERY PHONE (raphael, the
same XBL -> QHEE -> EL1 chain, the same xnu-8796.121.2) runs its A640 daily: its PAS_INIT_IMAGE
over the PACKED metadata (the vendor .mdt's shape: ELF header + phdrs + hash section, checked
52 + 32*segments + hashes == size, the code NOT among them) is ACCEPTED; the code goes to the
dt's own pil_gpu_region at 0x98f15000 (MEM_SETUP 0x1000); a640 never pre-sends the give-up.
Our rounds sent the two wrong forms -- R21's truncated page (no hash section) and R24+/M2's
whole .mbn (the 3948-byte gap and the code included) -- both refused 0x45.  R31 builds the
packed form from the stock file's own bytes (the header and the table verbatim, the hash
segment's p_offset rewritten from 0x1000 to where the blob now sits, the blob appended), and
writes the code to 0x98f15000.  The R30 state sweep (the SCM queue 0x85775e00+cpu*0x30, the
per-cpu handle cache 0x858a6390+cpu*0x18, the globals block 0x858a5900, the whole 64 KB SCM
region 0x85775000..0x85855000, the TZ DIAG region -- base = the u32 word at phys 0x146bf720,
0x3000 B -- and hyp's own diag, base = the u64 at 0x857619e8) rides at BOTH refusal paths:
the M2-era refusals died at step 12, where the R30 probe never ran.  The M2 build rides as
the (used, unused) archive; the N3 ring dump rides; the R28k run-goes-on wording rides; the
T2 strings all stay.  The loader is still v7 BYTE-IDENTICAL by design (sha ec15106d...), the
ramdisk still the R13 bytes VERBATIM.

This unpacks any r2x flash image (R14..R31 layout: bootimg v2 -> gzip(BootShim
+ UEFI FD + XNUP payload)) and prints the kernel, loader and ramdisk entries'
sha256 plus the generation markers, so "which kernel / which loader / which
volume / which cmdline" is arithmetic:

    python3 tools/r31-kernel-id.py <image> [image...]

Known kernel-entry sha256s (the rounds so far):
    R27 / R27b  372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c
    R28         9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3
    R28b        36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6
    R28c        same bytes as R28b (the kernel was unchanged; that round moved the loader)
    R28d        99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603 (run 40174469602, clean build)
    R28g        9cbcac8a4d66b1a64ec3e7b723055be367027ec190d9ef89b7c8bb0e106855a3 (run 40203429335, clean build; the lsd SIGBUS round)
    R28i        3a0d746dbc03136bd23bce1b82f6280b8bfd443b2e34ad8f93cfb3b6669fee53 (run 40234522508, clean build; the T2 ACCEPTED round)
    R28j        57db84e7400f1a353930e1a7ef4825ffd17ef3d0252c04efd3f77508479d6b82 (run 40292081321, clean build; the J1 round -- T2 0 + zap -3145797 in one boot)
    R28k        916d7719d400bfd69350b69df9fd3a0d082cc5c1cc007920184c58184eb6fe89 (run 40318421748, clean build; the refusal-continues round -- step 20 R28-first, K4 hit)
    R28l        no seat -- run 40331159223 died in its flash-build's own closing assert; no image ever existed
    R28m        911a69df7434ee76e004d2e5243b290cf73dc582e2f86f019bf5e986faf89c4e (run 40478487680, clean build; the M2/N3 round -- M2 held 0/52, the ring landed wrapper-only)
    R30         4a7088302cea1e81e044f531b4323690c617a1f271997aee7a8fbce0d255fe8f (run 40499304547, clean build; the swallowed-code recovery round -- the SCMDMP/TZDIAG sweep, superseded by R31's form fix before any flash)
    R31         (this round's build; its sha is printed and enters this table next round)

Known ramdisk-entry sha256s:
    R13..R28d   372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4  (the R13 bytes; R31 rides them VERBATIM)
    R28g        870650e3... / 9308eae1...  (the rebuilt volume of the round that died; retired)

A kernel cannot print a string its binary does not carry: markers below ARE the
runtime lines, counted in the bytes that would print them.
"""
import gzip, hashlib, struct, sys

FD_SIZE = 0x300000
KNOWN = {
    '372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c': 'R27/R27b (v5-loader round)',
    '57db84e7400f1a353930e1a7ef4825ffd17ef3d0252c04efd3f77508479d6b82': 'R28j (the T2 control, third edition, the SEQUENTIAL round: T2 BEFORE the zap attempt in one boot; the J1 round, run 40292081321)',
    '9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3': 'R28 (two-parameter baseline round)',
    '36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6': 'R28b (identity-audit round; R28c carries these same bytes by design)',
    '99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603': 'R28d (CPU-monitor waiver round; clean build, ccache 0/1036)',
    '9cbcac8a4d66b1a64ec3e7b723055be367027ec190d9ef89b7c8bb0e106855a3': 'R28g (the T2 control round; the lsd SIGBUS round, run 40203429335)',
    '3a0d746dbc03136bd23bce1b82f6280b8bfd443b2e34ad8f93cfb3b6669fee53': 'R28i (the T2 control, second edition: .mdt embedded + ON by default + the compiler pinned to theos clang v19 eabca92; the T2 ACCEPTED round, run 40234522508)',
    '916d7719d400bfd69350b69df9fd3a0d082cc5c1cc007920184c58184eb6fe89': 'R28k (the refusal-continues round, run 40318421748: step 20 without the zap R28-first, K4 hit, the lsd trigger face convicted to the adreno phase)',
    '911a69df7434ee76e004d2e5243b290cf73dc582e2f86f019bf5e986faf89c4e': 'R28m (the M2/N3 round, run 40478487680: M2 held at -3145797 with 0/52 canonical bytes, the N3 ring landed and decoded wrapper-only)',
    '4a7088302cea1e81e044f531b4323690c617a1f271997aee7a8fbce0d255fe8f': 'R30 (the swallowed-code recovery round, run 40499304547: the SCMDMP/TZDIAG sweep built and shipped, superseded by R31\'s form fix before any flash)',
    # R28l: no seat -- run 40331159223 died in its flash-build's closing assert; no image ever existed
}
KNOWN_RAMDISK = {
    '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4': 'R13..R28d (the R13 bytes; R31 rides them VERBATIM -- no rebuild this round)',
    '870650e32ee6694f18a1e8cbe7f2e1d97b131672d782e553b9428065856c9657': 'R28g (the sandbox-built rebuilt volume; retired)',
    '9308eae198d20a5d257c6dc317e1e0b3c09c3e520bcce1f2b364a63118842bbf': 'R28g (the CI-built rebuilt volume; retired)',
}

# (family, marker) -- the runtime lines, in the bytes that print them
MARKERS = [
    ('R31 generation line', b'R31: the kernel generation line'),
    ('R31 announce        ', b'R31: the fork-proven form'),
    ('R31 packed built     ', b'the packed metadata is built'),
    ('R31 p_offset rewrite ', b"the hash segment's p_offset rewritten"),
    ('R31 SCMDMP begin    ', b'SCMDMP: R31 begins'),
    ('R31 QUE entry        ', b'SCMDMP QUE cpu%d: the SCM queue entry'),
    ('R31 HND entry        ', b'SCMDMP HND cpu%d: the per-cpu handle cache entry'),
    ('R31 GLB block        ', b'SCMDMP GLB: phys 0x858a5900'),
    ('R31 SCM sweep        ', b'the SCM region, all %u bytes'),
    ('R31 TZREG word       ', b'SCMDMP TZREG: the TZ DIAG registry word'),
    ('R31 TZDIAG dump      ', b"TZDIAG: the TZ's own log"),
    ('R31 HDREG word       ', b'SCMDMP HDREG: the hyp DIAG registry word'),
    ('R31 HYPDIAG dump     ', b"HYPDIAG: the hypervisor's own diag buffer"),
    ('R31 scmsweep switch  ', b'adreno_scmsweep'),
    ('R31 M2 seg table    ', b'M2: seg %u: type'),
    ('R31 M2 canonical    ', b'M2: the header rewritten canonical'),
    ('R31 M2 chain verdict', b'M2: THE SIGNATURE CHAIN SAYS'),
    ('R31 N3 registry     ', b'HYPLOG: the registry at phys 0x146bfb30'),
    ('R31 N3 ring dump    ', b'HYPLOG: the ring, all %u bytes'),
    ('R31 N3 boot arg     ', b'adreno_hyplog'),
    ('R28k generation line', b'R28k: the kernel generation line'),
    ('R28l generation line', b'R28l: the kernel generation line'),
    ('R28l phase bisection ', b'R28l: the phase bisection'),
    ('R28k run-goes-on     ', b'R28k: the run goes on to the 3D core without the zap'),
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
        raise SystemExit('usage: r31-kernel-id.py <image> [image...]')
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
              + (f'   == {KNOWN[ksha]}' if ksha in KNOWN else '   == R31 (the fork-proven-form round -- a NEW sha, expected this round)'))
        if rmd:
            rsha = hashlib.sha256(rmd).hexdigest()
            print(f'  ramdisk sha256 : {rsha}   ({len(rmd):,} B)'
                  + (f'   == {KNOWN_RAMDISK[rsha]}' if rsha in KNOWN_RAMDISK else ''))
        if ldr:
            lsha = hashlib.sha256(ldr).hexdigest()
            if lsha.startswith('ec15106da4bad290'):
                tag = '   == XNULOADER v7 (the R28c build, device-validated; R31 carries it verbatim)'
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
        verdict = ('R31 (the fork-proven-form round: R31 generation stamps + the R31 announce + the packed-metadata '
                   'probe strings (QUE/HND/GLB/SCM/TZREG/TZDIAG/HDREG/HYPDIAG) all in the binary, the M2 seg '
                   'table + canonical rewrite + chain verdict and the N3 registry + ring dump lines riding '
                   'verbatim, the R28k run-goes-on wording riding (the refusal path reaches it now), the '
                   'R28m/R28k/R28l generation stamps absent; the loader is the R28c v7 verbatim, the ramdisk is '
                   'the R13..R28d volume VERBATIM)'
                   if k.count(b'R31: the kernel generation line') == 2
                   and k.count(b'R31: the fork-proven form') == 1
                   and k.count(b'the packed metadata is built') == 1
                   and k.count(b"the hash segment's p_offset rewritten") == 2
                   and k.count(b'SCMDMP: R31 begins') == 1
                   and k.count(b'SCMDMP QUE cpu%d: the SCM queue entry') == 1
                   and k.count(b'SCMDMP HND cpu%d: the per-cpu handle cache entry') == 1
                   and k.count(b'SCMDMP GLB: phys 0x858a5900') == 1
                   and k.count(b'the SCM region, all %u bytes') == 1
                   and k.count(b'SCMDMP TZREG: the TZ DIAG registry word') == 1
                   and k.count(b"TZDIAG: the TZ's own log") == 1
                   and k.count(b'SCMDMP HDREG: the hyp DIAG registry word') == 1
                   and k.count(b"HYPDIAG: the hypervisor's own diag buffer") == 1
                   and k.count(b'adreno_scmsweep') == 2
                   and k.count(b'M2: seg %u: type') == 1
                   and k.count(b'M2: the header rewritten canonical') == 1
                   and k.count(b'M2: THE SIGNATURE CHAIN SAYS') == 1
                   and k.count(b'HYPLOG: the registry at phys 0x146bfb30') == 1
                   and k.count(b'HYPLOG: the ring, all %u bytes') == 1
                   and k.count(b'adreno_hyplog') == 1
                   and k.count(b'R28k: the run goes on to the 3D core without the zap') == 1
                   and k.count(b'R28m: the kernel generation line') == 0
                   and k.count(b'R28m: the M2 contract build + the N3 white-box probe') == 0
                   and k.count(b'R28k: the kernel generation line') == 0
                   and k.count(b'R28j: the kernel generation line') == 0
                   and k.count(b'R28j: the two-parameter baseline') == 1
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
                   else 'R28l (never shipped: the r28l flash-build assert bug; no such image exists)' if k.count(b'R28l: the kernel generation line') == 2
                   else 'R28k (the refusal-continues round)' if k.count(b'R28k: the kernel generation line') == 2
                   and k.count(b'R28k: the run goes on to the 3D core without the zap') == 1
                   else 'R28j (the T2 control, third edition, the SEQUENTIAL round; the J1 round)' if k.count(b'R28j: the two-parameter baseline') == 1
                   and k.count(b'R28j: the kernel generation line') >= 1
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
            toks = cmd.rstrip(bytes([0])).decode(errors='replace').split()
            if b'adreno_test_adsp' in cmd:
                print('  NOTE: this cmdline carries an adreno_test_adsp token -- an R28g-era image, '
                      'or a hand edit; an R31 image needs NO token (the switch is the kernel default)')
            elif verdict.startswith('R31'):
                if 'gpu=1' in toks:
                    if 'adreno=22' in toks and 'adreno_skip' not in ' '.join(toks) and 'adreno_pause=0' in toks and 'adreno_watch=0' in toks:
                        print('  VERDICT: the cmdline is the GPU image WITH the full-step tokens -- correct for R31 '
                              '(ALL steps, the zap\'s step 19 included; the refusal must print the R31 announce, the '
                              'HYPLOG ring, and the SCMDMP/TZDIAG region dumps)')
                    else:
                        print('  NOTE: this cmdline carries gpu=1 WITHOUT the complete full-step token set '
                              '(adreno=22 adreno_pause=0 adreno_watch=0, no adreno_skip) -- an R28k/R28l-era GPU image, '
                              'or a hand edit; an R31 GPU image needs the full-phase set')
                else:
                    print('  VERDICT: the cmdline is the daily image WITHOUT any adreno token -- correct for R31 '
                          '(the whole adreno phase stays skipped; the R28k-proven control)')
        if rmd:
            # structural: R31's volume is the R28d one VERBATIM (no adsp.mbn in it -- the .mdt rides in the kernel)
            if hashlib.sha256(rmd).hexdigest() == '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4':
                print('  VERDICT: the ramdisk entry is R13..R28d VERBATIM (no fw/adsp.mbn -- correct for R31: '
                      'the .mdt rides inside the kernel, the volume is never asked)')
            elif b'adsp.mbn' in rmd and b'a640_zap.mbn' in rmd:
                print('  VERDICT: the ramdisk entry is R28g (the rebuilt volume; the R28G first-second death) -- NOT this round')
            else:
                print('  VERDICT: the ramdisk entry is UNRECOGNIZED (no adsp.mbn name, not the R13 bytes either)')
    print('=' * 96)


if __name__ == '__main__':
    main()
