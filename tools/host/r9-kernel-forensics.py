#!/usr/bin/env python3
"""R9 forensics: where does the SHIPPED XNU kernel reference the vtimer INTID?

R8 verdict (corrected): the kernel enables + groups GICR INTID 27 (the QEMU/
bcm2837 virtual-timer doorbell), but SM8150 wires the EL1 virtual timer at
GIC_PPI 3 = INTID 19 (mainline sm8150.dtsi).  The tick has been pending on 19
since R7 (pending 10080000 = bit28+bit19).  This tool finds every place the
prebuilt kernel hardcodes 27 / 0x08000000, so the R9 patch can retarget them.

Modes:
  sym  <pattern>          list symbols matching regex
  dump <symbol>           disassemble one symbol (same as r8 tool)
  scan                    whole-__TEXT scan:
                          - any instruction that materialises 0x08000000
                            (movz #0x800 lsl#16 / movk #0x800 lsl#16)
                          - cmp/tst/bits against 27 (0x1b) / 0x8000000
                          - msr/mrs of CNTV_* / CNTP_* / ICC_* sysregs
                          Each hit is attributed to the containing symbol.

Usage: python3 tools/host/r9-kernel-forensics.py [kernel] scan|sym|dump [arg]
Kernel defaults to prebuilt/mach.development.bcm2837 (repo-relative).
Needs capstone.
"""
import os
import re
import struct
import sys


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
    sys.exit('capstone missing: pip install capstone')


def parse_macho(data):
    """-> (sections, symbols): sections as (name, seg, vmaddr, size, fileoff),
    symbols as {name: addr} plus reverse map built later."""
    if struct.unpack_from('<I', data, 0)[0] != 0xfeedfacf:
        sys.exit('not a 64-bit Mach-O')
    ncmds = struct.unpack_from('<I', data, 16)[0]
    off = 32
    sections, symtab = [], (0, 0, 0)
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x19:                                   # LC_SEGMENT_64
            nsects = struct.unpack_from('<I', data, off + 64)[0]
            so = off + 72
            for _s in range(nsects):
                sect = data[so:so + 80]
                name = sect[0:16].rstrip(b'\0').decode()
                seg = sect[16:32].rstrip(b'\0').decode()
                addr, size = struct.unpack_from('<2Q', sect, 32)
                foff = struct.unpack_from('<I', sect, 48)[0]   # offset@48 (flags@64!)
                sections.append((name, seg, addr, size, foff))
                so += 80
        elif cmd == 0x2:                                  # LC_SYMTAB
            symtab = struct.unpack_from('<3I', data, off + 8)   # symoff, nsyms, stroff
        off += cmdsize
    symoff, nsyms, stroff = symtab
    symbols = {}
    for i in range(nsyms):
        e = symoff + 16 * i
        strx = struct.unpack_from('<I', data, e)[0]
        value = struct.unpack_from('<Q', data, e + 8)[0]
        if value == 0:
            continue
        end = data.index(b'\0', stroff + strx)
        symbols[data[stroff + strx:end].decode()] = value
    return sections, symbols


def build_containment(symbols):
    """-> sorted [(addr, name)] for attributing an address to a symbol."""
    pairs = sorted((a, n) for n, a in symbols.items())
    return pairs


def attribute(pairs, addr):
    """largest symbol addr <= addr"""
    lo, hi = 0, len(pairs)
    while lo < hi:
        mid = (lo + hi) // 2
        if pairs[mid][0] <= addr:
            lo = mid + 1
        else:
            hi = mid
    return pairs[lo - 1][1] if lo else '?'

SYSREGS = re.compile(
    r'icc_[a-z0-9_]+|ich_[a-z0-9_]+'
    r'|cntv_[a-z_]+|cntp_[a-z_]+|cnth[a-z_]*_[a-z_]+|cntfrq|cntct|cntkctl', re.I)

def main():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    kernel = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, 'prebuilt', 'mach.development.bcm2837')
    mode = sys.argv[2] if len(sys.argv) > 2 else 'scan'
    arg = sys.argv[3] if len(sys.argv) > 3 else None

    capstone = get_capstone()
    data = open(kernel, 'rb').read()
    sections, symbols = parse_macho(data)
    print(f'{kernel}: {len(symbols):,} symbols, {len(sections)} sections')

    if mode == 'sym':
        pat = re.compile(arg or 'timer|gic|clock|decrement|sleh|except|rtc|pet|wdt', re.I)
        for n, a in sorted(((n, a) for n, a in symbols.items() if pat.search(n)), key=lambda t: t[1]):
            print(f'0x{a:012x}  {n}')
        return

    if mode == 'dump':
        symbol = arg or '_gic_v3_irq_cpu_init'
        if symbol not in symbols:
            near = [n for n in symbols if symbol.strip('_') in n][:5]
            sys.exit(f'no symbol {symbol!r}' + (f'; near: {near}' if near else ''))
        addr = symbols[symbol]
        hit = next((s for s in sections if s[2] <= addr < s[2] + s[3]), None)
        if hit is None:
            sys.exit(f'{symbol} @ 0x{addr:x} is not in any section')
        name, seg, saddr, ssize, foff = hit
        higher = sorted(a for a in symbols.values() if a > addr)
        end = higher[0] if higher else addr + 0x400
        size = min(end - addr, 0x800)
        foff += addr - saddr
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
        print(f'--- {symbol} @ 0x{addr:x} ({name}/{seg}, +0x{size:x}) ---')
        for ins in md.disasm(data[foff:foff + size], addr):
            print(f'0x{ins.address:x}:  {ins.mnemonic}\t{ins.op_str}')
        return

    # ---- scan: whole executable text (__text of __TEXT_EXEC is the big one) ----
    text = max((s for s in sections if s[0] == '__text' and s[3] > 0x1000),
               key=lambda s: s[3])
    _, seg, saddr, ssize, foff = text
    pairs = build_containment(symbols)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    print(f'scanning __text 0x{saddr:x}+0x{ssize:x} ...')

    hits_27, hits_sys = [], []

    def walk(start_foff, start_va, length):
        """linear disasm that resumes past undecodable words (capstone stops
        silently at the first bad instruction otherwise)."""
        pos, n = 0, 0
        while pos < length:
            got = 0
            for ins in md.disasm(data[start_foff + pos:start_foff + length],
                                 start_va + pos):
                got = 1
                pos = ins.address + ins.size - start_va
                yield ins
            if not got:                      # undecodable word: skip 4 bytes
                pos += 4
                n += 1
        if n:
            print(f'(scan skipped {n} undecodable words)', file=sys.stderr)

    for ins in walk(foff, saddr, ssize):
        ops = ins.op_str
        m = ins.mnemonic
        # exact 0x08000000 materialisations (mov/movz/movk imm, not 0x8000/0x80000000)
        # or comparisons against 27 (0x1b)
        if re.search(r'#0x8000000\b', ops) or re.search(r'#0x800, lsl #16\b', ops):
            hits_27.append((ins.address, m, ops))
        elif m in ('cmp', 'cmn', 'subs') and '#0x1b' in ops:
            hits_27.append((ins.address, m, ops))
        elif m in ('msr', 'mrs') and SYSREGS.search(ops):
            hits_sys.append((ins.address, m, ops))

    print(f'\n=== 0x08000000 / #27 materialisations or comparisons: {len(hits_27)} ===')
    for a, m, ops in hits_27:
        print(f'0x{a:x}  [{attribute(pairs, a)}]  {m} {ops}')

    print(f'\n=== timer / GIC CPU-interface sysreg accesses: {len(hits_sys)} ===')
    for a, m, ops in hits_sys:
        print(f'0x{a:x}  [{attribute(pairs, a)}]  {m} {ops}')


if __name__ == '__main__':
    main()
