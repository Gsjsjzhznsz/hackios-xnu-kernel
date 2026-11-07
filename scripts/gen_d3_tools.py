#!/usr/bin/env python3
"""Generate D3 tools from the D1 and D2 tools, from the repository root."""
from pathlib import Path

D2_GPU = '8f44f076cf822c49d604159b3ef6fbe3bfa14af5b962449edd29eba5c86b9c61'
D2_KERNEL = '82a3e023b39476066a1c02c5d052412dc734eecf3f5fe954175ca044b31d9d0a'
D1 = 'D1: the stalled command buffers read aloud, word by word'
D3 = 'D3: the 3D core guard covers two megabytes for diagnosis'
if len(D1.encode()) != 56 or len(D3.encode()) != 56:
    raise ValueError('marker length')


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('expected one occurrence: %r (got %d)' % (old[:100], text.count(old)))
    return text.replace(old, new)


def block(text, start, end, replacement):
    a = text.index(start)
    b = text.index(end, a)
    return text[:a] + replacement + text[b:]


def output(path, text):
    text = once(text, 'import argparse\n', 'import sys\nif not __debug__:\n    sys.exit("D3 checks require Python without -O")\n\nimport argparse\n')
    Path(path).write_text(text, encoding='utf-8')
    print('generated', path)


def main():
    gate = Path('tools/d1_kernel_gate.py').read_text()
    gate = gate.replace('D1_MARKER', 'D3_MARKER').replace('R61_MARKER', 'OLD_MARKER')
    gate = once(gate, "D3_MARKER = b'%s'" % D1, "D3_MARKER = b'%s'" % D3)
    gate = once(gate, "OLD_MARKER = b'R61: the association answer read aloud in hex, all bytes'", "OLD_MARKER = b'%s'" % D1)
    gate = gate.replace('8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7', D2_KERNEL)
    gate = gate.replace('--r61-kernel', '--d2-kernel').replace('args.r61_kernel', 'args.d2_kernel')
    gate = block(gate, '    def allowed_gain(s):', '    bad_gain =', '''    def allowed_gain(s):
        return s == D3_MARKER or run_like(s) or DARWIN.search(s)

    def allowed_loss(s):
        return s == OLD_MARKER or run_like(s) or DARWIN.search(s)

''')
    gate = once(gate, '    # 4. segment census', '''    old_exec = [(name, old[off:off + size]) for name, off, size in segs_o if name == b'__TEXT_EXEC']
    new_exec = [(name, new[off:off + size]) for name, off, size in segs_n if name == b'__TEXT_EXEC']
    assert len(old_exec) == len(new_exec) == 1, 'missing or duplicate __TEXT_EXEC segment'
    assert old_exec[0][1] != new_exec[0][1], 'executable code is unchanged from D2'
    say('code law: executable segment differs from D2 (not proof of runtime behavior)')
    assert old.count(b'adreno: gpu: dump:') > 0, 'D2 lost D1 dump diagnostics'
    assert new.count(b'adreno: gpu: dump:') == old.count(b'adreno: gpu: dump:'), 'D1 dump diagnostics changed'
    old_dump = sorted(re.findall(rb'adreno: gpu: dump:[\\x20-\\x7e]*', old))
    new_dump = sorted(re.findall(rb'adreno: gpu: dump:[\\x20-\\x7e]*', new))
    assert old_dump == new_dump, 'D1 dump diagnostics changed'

    # 4. segment census''')
    gate = block(gate, '"""tools/', '"""\n\nimport', '"""D3 kernel gate: D2 baseline and six release laws.\n\nUsage: --base-kernel D2 --d2-kernel D3 [--report PATH]\n')
    gate = block(gate, "    say('GATE GREEN:", '    if args.report:', "    say('GATE GREEN: D3 passes all six laws against the pinned D2 kernel')\n")
    gate = gate.replace('r61 base', 'D2 base').replace('d1 kernel', 'D3 kernel').replace('the D1 marker', 'the D3 marker').replace('R61 marker', 'D1 marker')
    gate = gate.replace(' in d1', ' in D3').replace('r60 gains unexpected', 'D3 gains unexpected').replace('r60 loses unexpected', 'D3 loses unexpected')
    gate = gate.replace('the D3 marker retired', 'the D1 marker retired').replace('the D3 marker still rides', 'the D1 marker still rides')
    gate = gate.replace('the D3 marker did not show up as a masked loss', 'the D1 marker did not show up as a masked loss')
    gate = gate.replace('the D1 marker + the adreno: gpu: dump: words', 'the D3 marker').replace('the D1 marker + the adreno: gpu: dump: words', 'the D3 marker')
    gate = gate.replace('page-quantized -- the R52 quantum', 'bounded growth')
    output('tools/d3_kernel_gate.py', gate)

    build = Path('tools/d2_flash_build.py').read_text()
    build = build.replace('a4f1c330622acbe8bcc922e66eb1ef3a1b917d1af9c9d0e92d0ae929fdb81a45', D2_GPU)
    build = build.replace('85448240dab17fdebe11929d25de3a74c1211209f3f4e2c5fac1d423985c199f', D2_KERNEL)
    build = build.replace('D1_MARKER', 'D3_MARKER')
    build = once(build, "D3_MARKER = b'%s'" % D1, "D3_MARKER = b'%s'\nOLD_MARKER = b'%s'" % (D3, D1))
    build = once(build, '    assert kernel.count(R61_MARKER) == 0', "    assert kernel.count(OLD_MARKER) == 0, 'the D1 marker must be retired'\n    assert kernel.count(R61_MARKER) == 0")
    build = once(build, "    kernel = open(args.kernel, 'rb').read()", "    kernel = open(args.kernel, 'rb').read()\n    assert b'adreno: gpu: dump:' in kernel, 'D1 dump diagnostics must remain'")
    build = build.replace('D1 kernel', 'D3 kernel').replace('D1 build', 'D3 build').replace('D1 marker', 'D3 marker')
    build = build.replace('the D3 marker must be retired', 'the D1 marker must be retired').replace('kernel 85448240 (D1)', 'kernel 82a3e023 (D2)')
    build = build.replace('gpu: the base is not the r61 release GPU image', 'gpu: the base is not the pinned D2 GPU image').replace('gpu: the kernel is not the r61 release kernel (the D1 gate BASE)', 'gpu: the kernel is not the pinned D2 kernel').replace('byte-identical to the r61 base', 'byte-identical to the D2 base').replace("this round's kernel (the R61 source + the stall ledger + the dump)", 'D3 kernel')
    build = once(build, "    open(args.out_daily, 'wb').write(d_img)\n", '')
    build = once(build, "    open(args.out_gpu, 'wb').write(bytes(out))", "    open(args.out_daily, 'wb').write(d_img)\n    open(args.out_gpu, 'wb').write(bytes(out))")
    build = block(build, '"""tools/', '"""\n\nimport', '"""Pack a D3 GPU image using the pinned D2 image and D3 kernel.\n\nUsage: --daily PATH --gpu PATH --kernel PATH --out-daily PATH --out-gpu PATH\n')
    output('tools/d3_flash_build.py', build)

    pack = Path('tools/d1_flash_pack.py').read_text().replace('D1_MARK', 'D3_MARK')
    pack = once(pack, "D3_MARK = b'%s'" % D1, "D3_MARK = b'%s'\nOLD_MARK = b'%s'" % (D3, D1))
    pack = once(pack, '    assert R61_MARK not in kern', "    assert OLD_MARK not in kern, 'the D1 marker must be retired'\n    assert R61_MARK not in kern")
    pack = pack.replace('D1 kernel', 'D3 kernel').replace('D1 marker', 'D3 marker').replace('D1 CI', 'D3 CI').replace('d1 release', 'd3 release').replace('D1 forensic', 'D3 forensic').replace('(D1,', '(D3,').replace('D1 PRESENT', 'D3 PRESENT')
    pack = pack.replace('the D3 marker must be retired', 'the D1 marker must be retired')
    pack = once(pack, "    assert b'wifi=0' in p2 and b'gpu=1' in p2 and b'fb_scan=1' in p2, 'the D0b display tokens are not in the payload'\n    assert b'adreno=22' not in p2 and b'metal=1' not in p2, 'the adreno/metal five must stay out (the D0 single-variable law)'", "    packed_cmd = dict(parse_payload(p2))['cmdline']\n    assert tuple(cmdline_tokens(packed_cmd).split()) == DAILY_TOKENS + GPU_TOKENS, 'the session cmdline does not match the D0b display shape'")
    pack = block(pack, '"""tools/', '"""\nimport', '"""Pack a pinned D3 release with the D0b session cmdline.\n\nUsage: --base PATH --base-sha SHA --kernel-sha SHA --out-gpu PATH --out-xnup PATH\n')
    output('tools/d3_flash_pack.py', pack)

    ident = Path('tools/d1-kernel-id.py').read_text().replace('D1_MARKER', 'D3_MARKER')
    ident = once(ident, "D3_MARKER = b'%s'" % D1, "D3_MARKER = b'%s'\nD1_MARKER = b'%s'\nD2_SHA = '%s'" % (D3, D1, D2_KERNEL))
    ident = once(ident, "        k = ents.get('kernel', b'')", "        for required in ('loader.efi', 'kernel', 'ramdisk', 'cmdline'):\n            assert required in ents, 'missing required entry: ' + required\n        for name, expected in (('loader.efi', args.expect_loader_sha), ('kernel', args.expect_kernel_sha), ('ramdisk', args.expect_volume_sha)):\n            if expected is not None:\n                assert hashlib.sha256(ents[name]).hexdigest() == expected.lower(), name + ' sha256 does not match expectation'\n        k = ents['kernel']")
    ident = once(ident, '        else:\n            missing = [m for m in R38_KERNEL_MARKS if m not in k]\n            assert k.count', '''        elif ksha == D2_SHA:
            assert k.count(D1_MARKER) == 1 and k.count(D3_MARKER) == 0, 'D2 marker mismatch'
            print('  VERDICT: the kernel entry is D2 (pinned D2 kernel)')
        else:
            assert D1_MARKER not in k, 'D1 marker must be retired'
            assert b'adreno: gpu: dump:' in k, 'D1 dump diagnostics missing'
            missing = [m for m in R38_KERNEL_MARKS if m not in k]
            assert k.count''')
    ident = block(ident, "                print('  VERDICT: the kernel entry is D1", '        # ---- cmdline verdict', "                print('  VERDICT: the kernel entry is D3 (2 MiB diagnostic core guard)' + (', matches expected kernel SHA' if mine else ''))\n")
    ident = ident.replace("== the R61 kernel (this round's build: the R60 source + the evidence law -- the answer's elements in hex, the honest ask count)", '== the expected kernel SHA').replace('the D1 kernel sha (from the flash build)', 'the expected kernel sha256').replace('a new kernel without the D1 marker', 'a new kernel without the D3 marker')
    ident = ident.replace("            if missing:\n                print('  VERDICT: the kernel entry is a NEW kernel but MISSES R38 marks: %s'\n                      % ', '.join(m.decode(errors='replace') for m in missing))\n            else:\n", "            assert not missing, 'D3 kernel misses R38 marks: %s' % missing\n            if True:\n")
    ident = block(ident, '"""tools/', '"""\n\nimport', '"""Identify D3, pinned D2 and historical boot-image kernels.\n\nUsage: IMAGE [--expect-loader-sha SHA] [--expect-kernel-sha SHA] [--expect-volume-sha SHA]\n')
    output('tools/d3-kernel-id.py', ident)


if __name__ == '__main__':
    main()
