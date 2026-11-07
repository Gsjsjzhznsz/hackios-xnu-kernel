#!/usr/bin/env python3
"""Offline integration and negative controls; synthetic kernels are not D4 builds."""
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
IMAGE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT.parent / 'd3-materials/raphael-xnu-d3-gpu-flash.img'
DAILY = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT.parent / 'r54/raphael-xnu-r54-flash.img'
OLD = b'D3: the 3D core guard covers two megabytes for diagnosis'
NEW = b'D4: the GPU ledger reports its candidates safely at last'
D3_SHA = '58ae90f5b3beeba742580e9fac723fb3eeab5d58766e65c1f8983f1db55db9c6'
GPU_SHA = 'ea07f5e8a9ec582449a7518f537cd6b8b7a57a2062457f7307503974f68c95cf'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def run(*args, ok=True, needle=None, cwd=ROOT):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if (result.returncode == 0) != ok or (needle and needle not in result.stdout):
        raise AssertionError('%s returned %d:\n%s' % (args, result.returncode, result.stdout))
    print('PASS:', ' '.join(map(str, args)), '(rc=%d)' % result.returncode)


def main():
    spec = importlib.util.spec_from_file_location('build', ROOT / 'tools/d4_flash_build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    image, entries, _ = build.unpack(str(IMAGE))
    assert hashlib.sha256(image).hexdigest() == GPU_SHA
    base = entries['kernel']
    assert hashlib.sha256(base).hexdigest() == D3_SHA and base.count(OLD) == 1
    loader_sha = hashlib.sha256(entries['loader.efi']).hexdigest()
    for filename, variable in (('scripts/dispatch_d4.py', 'LOADER'), ('tools/d4_flash_pack.py', 'LOADER'), ('tools/d4_flash_build.py', 'R54_LOADER'), ('tools/d4-kernel-id.py', 'R39_LOADER')):
        tree = ast.parse((ROOT / filename).read_text())
        value = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == variable for target in node.targets))
        assert value == loader_sha, filename + ': loader SHA differs from actual image'
    print('PASS: all four loader constants match the D3 image')
    assert len(NEW) == len(OLD) == 56
    gate = ROOT / 'tools/d4_kernel_gate.py'
    ident = ROOT / 'tools/d4-kernel-id.py'
    pack = ROOT / 'tools/d4_flash_pack.py'
    with tempfile.TemporaryDirectory(prefix='d4-test-') as name:
        td = Path(name)
        old, new = td / 'base', td / 'synthetic'
        old.write_bytes(base)
        marker_only = base.replace(OLD, NEW)
        new.write_bytes(marker_only)
        run(gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='executable code is unchanged')
        test = bytearray(marker_only)
        off = 32
        ncmds = struct.unpack_from('<I', test, 16)[0]
        segments = {}
        symoff = None
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', test, off)
            if cmd == 0x19:
                segments[bytes(test[off + 8:off + 24]).rstrip(b'\0')] = (off, *struct.unpack_from('<QQ', test, off + 40))
            if cmd == 2:
                symoff = off
            off += size
        test[segments[b'__TEXT_EXEC'][1]] ^= 1
        test = bytes(test)
        new.write_bytes(test)
        run(gate, '--base-kernel', old, '--d4-kernel', new, needle='GATE GREEN')
        # Remove every old dump literal and replace one with a new dump literal.
        changed = bytearray(test)
        matches = list(re.finditer(rb'adreno: gpu: dump:[\x20-\x7e]*', test))
        assert matches
        for match in matches:
            changed[match.start():match.end()] = b'\0' * (match.end() - match.start())
        replacement = b'adreno: gpu: dump: D4 candidate'
        changed[matches[0].start():matches[0].start() + len(replacement)] = replacement
        changed = bytes(changed)
        new.write_bytes(changed)
        run(gate, '--base-kernel', old, '--d4-kernel', new, needle='GATE GREEN')
        new.write_bytes(test.replace(b'adreno: gpu: dump:', b'badxxx: gpu: dump:', 1))
        run(gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='unexpected strings')
        new.write_bytes(changed.replace(b'adreno: gpu: dump:', b'adreno: gpu: dXmp:', 1))
        run(gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='dump diagnostics missing')
        new.write_bytes(test.replace(NEW, OLD))
        run(gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='marker rides')
        run(gate, '--base-kernel', old, '--d4-kernel', old, ok=False, needle='byte-identical')
        run(gate, '--base-kernel', new, '--d4-kernel', new, ok=False, needle='not the release byte')
        corrupt = bytearray(test)
        struct.pack_into('<I', corrupt, symoff + 12, 0)
        new.write_bytes(corrupt)
        run(gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='FEWER symbols')
        corrupt = bytearray(test)
        struct.pack_into('<Q', corrupt, segments[b'__TEXT'][0] + 48, 0)
        new.write_bytes(corrupt)
        run(gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='__TEXT shrank')
        new.write_bytes(test)
        run('-O', gate, '--base-kernel', old, '--d4-kernel', new, ok=False, needle='without -O')
        run(ident, IMAGE, '--expect-kernel-sha', D3_SHA, needle='the kernel entry is D3')
        run(ident, IMAGE, '--expect-kernel-sha', '0' * 64, ok=False, needle='sha256 does not match expectation')
        run(pack, '--base', IMAGE, '--base-sha', GPU_SHA, '--kernel-sha', D3_SHA, '--out-gpu', td / 'bad.img', '--out-xnup', td / 'bad.xnup', ok=False, needle='D4 marker')
        out_daily, out_gpu, xnup = td / 'daily.img', td / 'gpu.img', td / 'session.xnup'
        run(ROOT / 'tools/d4_flash_build.py', '--daily', DAILY, '--gpu', IMAGE, '--kernel', new, '--out-daily', out_daily, '--out-gpu', out_gpu, needle='re-prove:')
        assert out_daily.read_bytes() == DAILY.read_bytes()
        built_image, built_entries, _ = build.unpack(str(out_gpu))
        assert built_entries['kernel'] == test
        for entry in ('loader.efi', 'ramdisk', 'cmdline', 'usb'):
            assert built_entries[entry] == entries[entry]
        ksha = hashlib.sha256(test).hexdigest()
        run(ident, out_gpu, '--expect-kernel-sha', ksha, needle='the kernel entry is D4')
        run(pack, '--base', out_gpu, '--base-sha', hashlib.sha256(built_image).hexdigest(), '--kernel-sha', ksha, '--out-gpu', td / 'session.img', '--out-xnup', xnup, needle='OK:')
        session = dict(build.parse_payload(xnup.read_bytes()))
        assert session['kernel'] == test and session['loader.efi'] == entries['loader.efi'] and session['ramdisk'] == entries['ramdisk'] and session['usb'] == entries['usb']
        assert tuple(build.cmdline_tokens(session['cmdline']).split()) == build.DAILY_TOKENS + ('gpu=1', 'fb_scan=1', 'wifi=0')
        # Extract the exact embedded pre-gate without executing dispatch.
        tree = ast.parse((ROOT / 'scripts/dispatch_d4.py').read_text())
        pre = next(ast.literal_eval(value) for node in ast.walk(tree) if isinstance(node, ast.Dict) for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant) and key.value == 'pre_gate_py')
        pre_path = td / 'pre.py'; pre_path.write_text(pre)
        paths = ('iokit/Kernel/RedwoodGPU.cpp', 'bsd/dev/raphael_adreno.c', 'iokit/Kernel/MiniIOMobileFramebuffer.cpp')
        for prefix in ('xnu/files', 'xnu-src/xnu-8796.121.2'):
            for path in paths:
                dest = td / prefix / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT / 'xnu/files' / path).read_bytes())
        run(pre_path, new, cwd=td, needle='D4 pre-gate passed')
        built_src = td / 'xnu-src/xnu-8796.121.2/iokit/Kernel/RedwoodGPU.cpp'
        original = built_src.read_bytes(); built_src.write_bytes(original + b'\n')
        run(pre_path, new, cwd=td, ok=False, needle='built source differs from overlay')
        built_src.write_bytes(original)
        new.write_bytes(base)
        run(pre_path, new, cwd=td, ok=False, needle='D4 marker missing or duplicated')
        new.write_bytes(test)
        guard_path = td / 'xnu/files/bsd/dev/raphael_adreno.c'
        guard = guard_path.read_bytes().replace(b'#define CORE_GUARD_SIZE         0x200000u', b'#define CORE_GUARD_SIZE         0x004000u')
        guard_path.write_bytes(guard)
        (td / 'xnu-src/xnu-8796.121.2/bsd/dev/raphael_adreno.c').write_bytes(guard)
        run(pre_path, new, cwd=td, ok=False, needle='D3 guard size changed')
        print('Synthetic mutation exercises gate and packing only; no real D4 kernel was built or validated.')


if __name__ == '__main__':
    main()
