#!/usr/bin/env python3
"""R9 kernel patch: point the prebuilt XNU kernel at SM8150's real vtimer doorbell.

WHAT R8 ACTUALLY MEASURED (the verdict, corrected):

  R8's "IGROUPR0 0 -> 0: the write did not stick" proved the READ is blind
  (RAZ), not that the write was dropped: Linux mainline writes the very same
  register from Non-secure EL1 every boot (irq-gic-v3.c:1293, "Configure
  SGIs/PPIs as non-secure Group-1") and raphael-Linux runs daily on this SoC.
  What R7/R8 actually caught was the PENDING line all along:

      enabled 8000000        -- the kernel enabled INTID 27
      pending 10080000       -- the tick pends on bit 28 + bit 19

  bit 19 = INTID 19 = the EL1 virtual timer on SM8150 (mainline
  arch/arm64/boot/dts/qcom/sm8150.dtsi: timer { <GIC_PPI 3 ...> /* virt */ }).
  bit 27 = INTID 27 = the QEMU/bcm2837 vtimer (the world the prebuilt
  mach.development.bcm2837 was born in).  The kernel rings doorbell 27; the
  silicon wired the bell at 19.  The tick has been pending on 19 since R7
  while the kernel waits at 27 -- and the group was never the story.

THE PATCH (4 sites, imm fields only, found in the shipped binary by the
r9-kernel-forensics scan, semantics asserted on both ends by capstone):

  _gic_v3_irq_cpu_init @ 0xfffffff007474798
    S1 0x...493c  movk w8, #0x800, lsl #16   (>=2 cpus path: 0x08000003)  -> #0x008
    S2 0x...4944  mov  w8, #0x8000000        (< 2 cpus path: 0x08000000)  -> #0x80000
    S3 0x...4968  strb w10, [x9, #0x39b]     (IPRIORITYR(27) = 0xa0)      -> #0x393 (IPRIORITYR(19))
  _gic_irq_handler @ 0xfffffff007474a5c
    S4 0x...4b04  cmp  w20, #0x1b            (the dispatch: INTID == vtimer) -> #0x13

  The IGROUPR0/IGRPMODR0/ISENABLER0 writes and the ICC_PMR=0xff /
  ICC_IGRPEN1=1 pair in the same function use the same w8 bit or are
  INTID-independent, so they follow the immediate for free.  Everything else
  -- CNTV arming (_ml_set_decrementer), EOI, SGI machinery -- is
  INTID-independent.

Usage:  python3 tools/r9-kernel-patch.py [kernel-in] [kernel-out]
        in defaults to prebuilt/mach.development.bcm2837 (repo-relative)
        out defaults to <in>.r9
Exits non-zero on ANY assertion failure; a half-patched file is never left.
Needs capstone (verification only; the patch itself is raw bytes).
"""
import os
import struct
import sys

VTIMER_QEMU = 27          # the doorbell the kernel was born with
VTIMER_SM8150 = 19        # GIC_PPI 3: where SM8150 actually wired it

SITES = [
    # (symbol, va, kind, orig_text, patched_text, imm_bits_lo, orig_imm, new_imm, comment)
    ('_gic_v3_irq_cpu_init', 0xfffffff00747493c, 'movk16', '#0x800, lsl #16', '#8, lsl #16', 5, 0x800, 0x008,
     'w8 = 0x08000003 (SGI0|SGI1|vtimer) on >=2-cpu paths'),
    ('_gic_v3_irq_cpu_init', 0xfffffff007474944, 'movz16', '#0x8000000', '#0x80000', 5, 0x800, 0x008,
     'w8 = 0x08000000 (vtimer only) on the 1-cpu path this chain takes (cpus=1)'),
    ('_gic_v3_irq_cpu_init', 0xfffffff007474968, 'strb12', '#0x39b', '#0x393', 10, 0x39b, 0x393,
     'IPRIORITYR byte: 0x380+27 -> 0x380+19 (priority 0xa0 to the real doorbell)'),
    ('_gic_irq_handler',     0xfffffff007474b04, 'cmp12',  '#0x1b', '#0x13', 10, 0x1b, 0x13,
     'the dispatch: INTID == vtimer -> rtclock_intr'),
]


def get_capstone():
    try:
        import capstone
        return capstone
    except ImportError:
        pass
    for ver in ('3.13', '3.12', '3.11'):
        p = os.path.expanduser(f'~/.local/lib/python{ver}/site-packages')
        if os.path.isdir(p):
            sys.path.insert(0, p)
            try:
                import capstone
                return capstone
            except ImportError:
                continue
    sys.exit('capstone missing: pip install capstone (needed to verify both ends)')


def section_of(data, va):
    """-> file offset for a VM address, from section headers (offset field @48)."""
    if struct.unpack_from('<I', data, 0)[0] != 0xfeedfacf:
        sys.exit('not a 64-bit Mach-O')
    ncmds = struct.unpack_from('<I', data, 16)[0]
    off = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x19:                                   # LC_SEGMENT_64
            nsects = struct.unpack_from('<I', data, off + 64)[0]
            so = off + 72
            for _s in range(nsects):
                sect = data[so:so + 80]
                saddr, ssize = struct.unpack_from('<2Q', sect, 32)
                sfoff = struct.unpack_from('<I', sect, 48)[0]
                if saddr <= va < saddr + ssize:
                    return sfoff + (va - saddr)
                so += 80
        off += cmdsize
    sys.exit(f'va 0x{va:x} is not in any section')


def one_instruction(capstone, word, va):
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for ins in md.disasm(struct.pack('<I', word), va):
        return ins
    sys.exit(f'word 0x{word:08x} @ 0x{va:x} does not decode')


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, 'prebuilt', 'mach.development.bcm2837')
    dst = sys.argv[2] if len(sys.argv) > 2 else src + '.r9'
    capstone = get_capstone()
    data = bytearray(open(src, 'rb').read())
    orig = bytes(data)
    patched_words = []

    print(f'r9-kernel-patch: {src} ({len(data):,} B) -> {dst}')
    for sym, va, kind, otext, ptext, lo, oimm, nimm, comment in SITES:
        fo = section_of(orig, va)
        word = struct.unpack_from('<I', orig, fo)[0]
        ins = one_instruction(capstone, word, va)
        got = f'{ins.op_str}'
        if otext not in got:
            sys.exit(f'S{SITES.index((sym, va, kind, otext, ptext, lo, oimm, nimm, comment)) + 1} @ 0x{va:x} '
                     f'expected ...{otext!r}, binary says {ins.mnemonic} {got!r} -- wrong kernel?')
        width = 16 if kind.startswith('mov') else 12      # imm16 (movz/movk) vs imm12 (strb/cmp)
        mask = ((1 << width) - 1) << lo
        new = (word & ~mask) | (nimm << lo)
        pins = one_instruction(capstone, new, va)
        if ptext not in pins.op_str:
            sys.exit(f'S{SITES.index((sym, va, kind, otext, ptext, lo, oimm, nimm, comment)) + 1} patched @ 0x{va:x} '
                     f'reassembles to {pins.mnemonic} {pins.op_str!r}, expected ...{ptext!r}')
        struct.pack_into('<I', data, fo, new)
        patched_words.append((va, fo, word, new, ins, pins, comment))
        print(f'  S{len(patched_words)} [{sym}] 0x{va:x}: {ins.mnemonic} {ins.op_str}  ->  {pins.mnemonic} {pins.op_str}')

    # exactly four words may differ, and only these four
    diffs = [i for i in range(0, len(orig), 4) if orig[i:i + 4] != bytes(data[i:i + 4])]
    if len(diffs) != 4 or {struct.unpack_from('<I', orig, d)[0] for d in diffs} != {w[2] for w in patched_words}:
        sys.exit(f'diff audit failed: {len(diffs)} words differ (expected exactly 4)')

    import hashlib
    open(dst, 'wb').write(bytes(data))
    print(f'  4 words patched, {len(orig) - 16} bytes of 16 changed-adjacent... '
          f'({sum(a != b for a, b in zip(orig, bytes(data)))} raw bytes differ total)')
    print(f'  orig  sha256 {hashlib.sha256(orig).hexdigest()}')
    print(f'  r9    sha256 {hashlib.sha256(bytes(data)).hexdigest()}')
    print('  doorbell: INTID 27 (QEMU/bcm2837) -> 19 (SM8150 GIC_PPI 3, mainline sm8150.dtsi)')


if __name__ == '__main__':
    main()
