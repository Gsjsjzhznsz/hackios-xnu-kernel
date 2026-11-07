#!/usr/bin/env python3
"""Generate D4 tools from checked-in D3 tools."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
D3_GPU = 'ea07f5e8a9ec582449a7518f537cd6b8b7a57a2062457f7307503974f68c95cf'
D3_KERNEL = '58ae90f5b3beeba742580e9fac723fb3eeab5d58766e65c1f8983f1db55db9c6'
D3 = 'D3: the 3D core guard covers two megabytes for diagnosis'
D4 = 'D4: the GPU ledger reports its candidates safely at last'
D1 = 'D1: the stalled command buffers read aloud, word by word'
assert len(D3.encode()) == len(D4.encode()) == 56


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('expected one occurrence of %r, got %d' % (old, text.count(old)))
    return text.replace(old, new)


def emit(name, text):
    (ROOT / name).write_text(text, encoding='utf-8')
    print('generated', name)


def main():
    gate = (ROOT / 'tools/d3_kernel_gate.py').read_text()
    gate = gate.replace('D3_MARKER', 'D4_MARKER').replace(D3, D4).replace(D1, D3)
    gate = gate.replace('82a3e023b39476066a1c02c5d052412dc734eecf3f5fe954175ca044b31d9d0a', D3_KERNEL)
    gate = gate.replace('D2', 'D3').replace('D1', 'D3').replace('D3 kernel:', 'D4 kernel:')
    gate = gate.replace('--d2-kernel', '--d4-kernel').replace('args.d2_kernel', 'args.d4_kernel')
    gate = gate.replace('D3 checks require', 'D4 checks require').replace('D3 kernel gate:', 'D4 kernel gate:')
    gate = gate.replace('the D3 marker', 'the D4 marker').replace(' in D3', ' in D4').replace('D3 gains', 'D4 gains').replace('D3 loses', 'D4 loses')
    gate = gate.replace('the D4 marker retired', 'the D3 marker retired').replace('the D4 marker still rides', 'the D3 marker still rides').replace('the D4 marker did not show up as a masked loss', 'the D3 marker did not show up as a masked loss')
    start = gate.index("    assert old.count(b'adreno: gpu: dump:')")
    end = gate.index('    # 4. segment census', start)
    gate = gate[:start] + "    assert b'adreno: gpu: dump:' in old and b'adreno: gpu: dump:' in new, 'dump diagnostics missing'\n\n" + gate[end:]
    gate = once(gate, 'return s == D4_MARKER or run_like(s) or DARWIN.search(s)', "return s == D4_MARKER or s.startswith(b'adreno: gpu: dump:') or run_like(s) or DARWIN.search(s)")
    gate = once(gate, 'return s == OLD_MARKER or run_like(s) or DARWIN.search(s)', "return s == OLD_MARKER or s.startswith(b'adreno: gpu: dump:') or run_like(s) or DARWIN.search(s)")
    gate = gate.replace("the D4 marker must keep the D4 marker byte length", "the D4 marker must keep the D3 marker byte length")
    gate = gate.replace("the D3 kernel is byte-identical to the r61 release", "the D4 kernel is byte-identical to the D3 release")
    gate = gate.replace("lost %d (the D4 marker + %d Darwin stamp lines + %d layout-run forms)", "lost %d (the D3 marker + %d Darwin stamp lines + %d layout-run forms)")
    gate = gate.replace('GATE GREEN: D3 passes all six laws against the pinned D3 kernel', 'GATE GREEN: D4 passes all six laws against the pinned D3 kernel')
    gate = gate.replace('Usage: --base-kernel D3 --d4-kernel D3', 'Usage: --base-kernel D3 --d4-kernel D4')
    emit('tools/d4_kernel_gate.py', gate)

    build = (ROOT / 'tools/d3_flash_build.py').read_text()
    build = build.replace('8f44f076cf822c49d604159b3ef6fbe3bfa14af5b962449edd29eba5c86b9c61', D3_GPU)
    build = build.replace('82a3e023b39476066a1c02c5d052412dc734eecf3f5fe954175ca044b31d9d0a', D3_KERNEL)
    build = build.replace('D3_MARKER', 'D4_MARKER').replace(D3, D4).replace(D1, D3)
    build = build.replace('D3', 'D4').replace('D2', 'D3').replace('D1', 'D3')
    build = build.replace('D4: the 3D core guard covers two megabytes for diagnosis', D3)
    build = build.replace('kernel 82a3e023 (D3)', 'kernel 58ae90f5 (D3)')
    emit('tools/d4_flash_build.py', build)

    pack = (ROOT / 'tools/d3_flash_pack.py').read_text()
    loader = (ROOT / 'tools/d3_flash_build.py').read_text().split("R54_LOADER = '", 1)[1].split("'", 1)[0]
    assert len(loader) == 64
    pack = once(pack, next(line for line in pack.splitlines() if line.startswith('LOADER = ')), "LOADER = %r" % loader)
    pack = pack.replace('D3_MARK', 'D4_MARK').replace(D3, D4).replace(D1, D3)
    pack = pack.replace('D3', 'D4').replace('D1', 'D3')
    pack = re.sub(r'(?<![0-9a-f])d3', 'd4', pack)
    pack = pack.replace('D4: the 3D core guard covers two megabytes for diagnosis', D3)
    emit('tools/d4_flash_pack.py', pack)

    ident = (ROOT / 'tools/d3-kernel-id.py').read_text()
    ident = ident.replace('D3_MARKER', 'D4_MARKER').replace(D3, D4)
    ident = once(ident, "D2_SHA = '", "D3_MARKER = b'%s'\nD3_SHA = '%s'\nD2_SHA = '" % (D3, D3_KERNEL))
    ident = once(ident, "assert k.count(D1_MARKER) == 1 and k.count(D4_MARKER) == 0", "assert k.count(D1_MARKER) == 1 and k.count(D3_MARKER) == 0 and k.count(D4_MARKER) == 0")
    ident = once(ident, '        else:\n            assert D1_MARKER not in k', "        elif ksha == D3_SHA:\n            assert k.count(D3_MARKER) == 1 and D1_MARKER not in k and D4_MARKER not in k, 'D3 marker mismatch'\n            print('  VERDICT: the kernel entry is D3 (pinned D3 kernel)')\n        else:\n            assert D3_MARKER not in k, 'D3 marker must be retired'\n            assert D1_MARKER not in k")
    ident = ident.replace('D3 checks require', 'D4 checks require').replace('Identify D3, pinned D2', 'Identify D4, pinned D3, D2')
    ident = ident.replace('D3 kernel misses R38', 'D4 kernel misses R38').replace('without the D3 marker', 'without the D4 marker').replace('the kernel entry is D3 (2 MiB diagnostic core guard)', 'the kernel entry is D4 (GPU ledger diagnostic)')
    emit('tools/d4-kernel-id.py', ident)

    dispatch = (ROOT / 'scripts/dispatch_d3.py').read_text()
    dispatch = once(dispatch, next(line for line in dispatch.splitlines() if line.startswith('LOADER = ')), "LOADER = %r" % loader)
    dispatch = dispatch.replace('D2_GPU', 'D3_GPU').replace('D2_KERNEL', 'D3_KERNEL')
    dispatch = dispatch.replace('8f44f076cf822c49d604159b3ef6fbe3bfa14af5b962449edd29eba5c86b9c61', D3_GPU).replace('82a3e023b39476066a1c02c5d052412dc734eecf3f5fe954175ca044b31d9d0a', D3_KERNEL)
    dispatch = dispatch.replace(D3, D4).replace(D1, D3)
    dispatch = dispatch.replace('D3', 'D4').replace('D2', 'D3')
    dispatch = re.sub(r'(?<![0-9a-f])d3', 'd4', dispatch)
    dispatch = re.sub(r'(?<![0-9a-f])d2', 'd3', dispatch)
    dispatch = dispatch.replace('D4: the 3D core guard covers two megabytes for diagnosis', D3)
    dispatch = dispatch.replace('D4_GPU', 'D3_GPU').replace('D4_KERNEL', 'D3_KERNEL')
    dispatch = dispatch.replace('D4: 2 MiB GPU guard diagnostic', 'D4: GPU ledger diagnostic')
    dispatch = dispatch.replace('--d3-kernel', '--d4-kernel')
    start = dispatch.index("        'pre_gate_py':")
    end = dispatch.index("        'kernel_gate_cmd':", start)
    pre = '''import pathlib, sys
b = pathlib.Path(sys.argv[1]).read_bytes()
root = pathlib.Path('xnu/files')
built = pathlib.Path('xnu-src/xnu-8796.121.2')
for path in ('iokit/Kernel/RedwoodGPU.cpp', 'bsd/dev/raphael_adreno.c', 'iokit/Kernel/MiniIOMobileFramebuffer.cpp'):
    assert (root / path).read_bytes() == (built / path).read_bytes(), 'built source differs from overlay: ' + path
src = (root / 'bsd/dev/raphael_adreno.c').read_bytes()
assert src.count(b'#define CORE_GUARD_ADDRESS      0x00000000u') == 1, 'D3 guard address changed'
assert src.count(b'#define CORE_GUARD_SIZE         0x200000u') == 1, 'D3 guard size changed'
assert b.count(%r) == 1, 'D4 marker missing or duplicated'
assert %r not in b and %r not in b, 'old marker present'
assert b.count(b'R33: the kernel generation line') == 2
assert b'adreno: gpu: dump:' in b
print('D4 pre-gate passed')''' % (D4.encode(), D3.encode(), D1.encode())
    dispatch = dispatch[:start] + "        'pre_gate_py': " + repr(pre) + ',\n' + dispatch[end:]
    dispatch = once(dispatch, "'release_readme': 'D4 diagnostic: 2 MiB writable GPU guard; D3 is the rollback baseline. This build is experimental and has not been device-validated.'", "'release_readme': 'D4 GPU ledger diagnostic; D3 is the rollback baseline. Experimental and not device-validated.'")
    dispatch = once(dispatch, "'release_body': 'D4 diagnostic build. GPU guard expands from 16 KiB to 2 MiB; the D3 fault and its cause remain under investigation.'", "'release_body': 'D4 ledger diagnostic build; the D3 2 MiB guard remains unchanged.'")
    emit('scripts/dispatch_d4.py', dispatch)


if __name__ == '__main__':
    main()
