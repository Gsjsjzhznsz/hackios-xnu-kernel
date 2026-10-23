#!/usr/bin/env python3
"""R8 forensics: disassemble a symbol in the SHIPPED (prebuilt) XNU kernel and
report what is actually in the delivered binary -- not what the sources say.

This settled the R8 question without a device: does the shipped kernel contain
the GICR_IGROUPR0 group write that xnu/files/osfmk/arm64/gic_v3_irq.c:477
shows?  Answer: yes --

    _gic_v3_irq_cpu_init @ 0xfffffff007474798
    (prebuilt/mach.development.bcm2837, symbols intact):
      w22 = 0x10080 (GICR_IGROUPR0);  w8 = 0x8000000 (1 << 27, vtimer PPI)
      ldr/orr/str [x9]          -- GICR_IGROUPR0 |= mine      (line 477)
      ldr/bic/str [x9,#0xc80]   -- GICR_IGRPMODR0 &= ~mine    (line 478)
      strb       [x9,#0x39b]    -- IPRIORITYR(27)
      str  w8,   [x9,#0x80]     -- GICR_ISENABLER0 = mine     (line 480)

...four instructions before the ISENABLER0 write whose landing R7's dump saw
as enabled=0x08000000.  Same frame, same execution: the enable carried, the
group write did not -> the GIC drops Non-secure writes to the group registers
while their neighbours land, i.e. it runs secure (DS=0) and the group bank is
secure-only (writes WI, reads RAZ/0).  Consequences in docs/FLASH-R8.md.

Usage:  python3 tools/host/r8-kernel-disasm.py [kernel] [symbol]
        kernel  default prebuilt/mach.development.bcm2837 (repo-relative)
        symbol  default _gic_v3_irq_cpu_init

Needs capstone (pip install capstone).  Reads the Mach-O symtab straight out
of __LINKEDIT; no external toolchain.
"""
import os
import struct
import sys


def get_capstone():
    try:
        import capstone
        return capstone
    except ImportError:
        pass
    # common user-site locations before falling over
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
    symbols as {name: addr} for every symbol with a value."""
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
                foff = struct.unpack_from('<I', sect, 48)[0]   # offset@48; 64 is the flags field!
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


def main():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    kernel = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, 'prebuilt', 'mach.development.bcm2837')
    symbol = sys.argv[2] if len(sys.argv) > 2 else '_gic_v3_irq_cpu_init'

    capstone = get_capstone()
    data = open(kernel, 'rb').read()
    sections, symbols = parse_macho(data)
    print(f'{kernel}: {len(symbols):,} symbols, {len(sections)} sections')

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
    print(f'--- {symbol} @ 0x{addr:x} ({name}/{seg}, +0x{size:x} bytes) ---')
    for ins in md.disasm(data[foff:foff + size], addr):
        print(f'0x{ins.address:x}:  {ins.mnemonic}\t{ins.op_str}')


if __name__ == '__main__':
    main()
