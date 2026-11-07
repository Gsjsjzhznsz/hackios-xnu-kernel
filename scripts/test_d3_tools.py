#!/usr/bin/env python3
"""Offline tests using a D2 image; synthetic D3 is not a real D3 build."""
import gzip
import hashlib
import importlib.util
import struct
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
IMAGE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT.parent / 'd3-materials/raphael-xnu-d2-gpu-flash.img'
D2 = '82a3e023b39476066a1c02c5d052412dc734eecf3f5fe954175ca044b31d9d0a'
OLD = b'D1: the stalled command buffers read aloud, word by word'
NEW = b'D3: the 3D core guard covers two megabytes for diagnosis'


def run(*args, ok=True, needle=None):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if (result.returncode == 0) != ok or (needle and needle not in result.stdout):
        raise AssertionError('%s returned %d:\n%s' % (args, result.returncode, result.stdout))
    print('PASS:', ' '.join(map(str, args)), '(rc=%d)' % result.returncode)
    return result.stdout


def main():
    spec = importlib.util.spec_from_file_location('build', ROOT / 'tools/d3_flash_build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    image, entries, _ = build.unpack(str(IMAGE))
    assert hashlib.sha256(image).hexdigest() == build.R61_GPU
    base = entries['kernel']
    assert hashlib.sha256(base).hexdigest() == D2 and base.count(OLD) == 1
    test = base.replace(OLD, NEW)
    assert len(test) == len(base)
    with tempfile.TemporaryDirectory(prefix='d3-test-') as td:
        td = Path(td)
        old = td / 'base'; old.write_bytes(base)
        new = td / 'synthetic'; new.write_bytes(test)
        gate = ROOT / 'tools/d3_kernel_gate.py'
        ident = ROOT / 'tools/d3-kernel-id.py'
        run(gate, '--base-kernel', old, '--d2-kernel', new, needle='GATE GREEN')
        run(gate, '--base-kernel', old, '--d2-kernel', old, ok=False, needle='byte-identical')
        run(gate, '--base-kernel', new, '--d2-kernel', new, ok=False, needle='not the release byte')
        new.write_bytes(test.replace(NEW, OLD))
        run(gate, '--base-kernel', old, '--d2-kernel', new, ok=False)
        new.write_bytes(test.replace(b'adreno: gpu: dump:', b'adreno: gpu: dXmp:', 1))
        run(gate, '--base-kernel', old, '--d2-kernel', new, ok=False, needle='dump diagnostics changed')
        changed = bytearray(test)
        ncmds = struct.unpack_from('<I', changed, 16)[0]
        off = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', changed, off)
            if cmd == 2:
                struct.pack_into('<I', changed, off + 12, 0)
                break
            off += size
        else:
            raise AssertionError('missing LC_SYMTAB')
        new.write_bytes(changed)
        run(gate, '--base-kernel', old, '--d2-kernel', new, ok=False, needle='FEWER symbols')
        changed = bytearray(test)
        off = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', changed, off)
            if cmd == 0x19 and changed[off + 8:off + 24].rstrip(b'\0') == b'__TEXT':
                struct.pack_into('<Q', changed, off + 48, 0)
                break
            off += size
        else:
            raise AssertionError('missing __TEXT')
        new.write_bytes(changed)
        run(gate, '--base-kernel', old, '--d2-kernel', new, ok=False, needle='__TEXT shrank')
        blob = gzip.decompress(image[4096:4096 + struct.unpack_from('<I', image, 8)[0]])
        x = blob.index(b'XNUP')
        order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
        payload = build.build_payload([[n, test if n == 'kernel' else entries[n]] for n in order])
        blob_new = blob[:x] + payload
        blob_new += b'\0' * (-len(blob_new) % 16)
        compressed = gzip.compress(blob_new, 6)
        synthetic_image = bytearray(image[:4096])
        struct.pack_into('<I', synthetic_image, 8, len(compressed))
        synthetic_image += compressed + b'\0' * (-len(compressed) % 4096)
        _, _, (ro, rs, do, ds) = build.unpack(str(IMAGE))
        synthetic_image += image[ro:ro + rs] + b'\0' * (-rs % 4096) + image[do:do + ds]
        synthetic_path = td / 'synthetic.img'; synthetic_path.write_bytes(synthetic_image)
        ksha = hashlib.sha256(test).hexdigest()
        run(ident, synthetic_path, '--expect-kernel-sha', ksha, needle='the kernel entry is D3')
        run(ROOT / 'tools/d3_flash_pack.py', '--base', synthetic_path, '--base-sha', hashlib.sha256(synthetic_image).hexdigest(), '--kernel-sha', ksha, '--out-gpu', td / 'session.img', '--out-xnup', td / 'session.xnup', needle='OK:')
        run(ROOT / 'tools/d3_flash_pack.py', '--base', IMAGE, '--base-sha', hashlib.sha256(image).hexdigest(), '--kernel-sha', D2, '--out-gpu', td / 'bad.img', '--out-xnup', td / 'bad.xnup', ok=False, needle='D3 marker')
        run(ident, IMAGE, '--expect-kernel-sha', D2, needle='the kernel entry is D2')
        run(ident, IMAGE, '--expect-kernel-sha', '0' * 64, ok=False, needle='sha256 does not match expectation')
        run('-O', gate, '--base-kernel', old, '--d2-kernel', new, ok=False, needle='without -O')
    print('Synthetic marker-only kernel does not establish D3 guard behavior.')


if __name__ == '__main__':
    main()
