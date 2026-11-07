#!/usr/bin/env python3
"""Dispatch D4 CI at the current committed and pushed main revision."""
import json
import subprocess
import urllib.request

REPO = 'Gsjsjzhznsz/hackios-xnu-kernel'
BASE = 'https://github.com/' + REPO + '/releases/download/d3-springboard/'
D3_GPU = 'ea07f5e8a9ec582449a7518f537cd6b8b7a57a2062457f7307503974f68c95cf'
D3_KERNEL = '58ae90f5b3beeba742580e9fac723fb3eeab5d58766e65c1f8983f1db55db9c6'
LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
MARKER = 'D4: the GPU ledger reports its candidates safely at last'
OLD_MARKER = 'D3: the 3D core guard covers two megabytes for diagnosis'


def git(*args, **kwargs):
    return subprocess.run(['git', *args], check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def main():
    if git('status', '--porcelain'):
        raise RuntimeError('commit tracked and untracked changes before dispatch')
    head = git('rev-parse', 'HEAD')
    remote = git('ls-remote', 'origin', 'refs/heads/main').split()[0]
    if head != remote:
        raise RuntimeError('HEAD does not match origin main')
    creds = git('credential', 'fill', input='protocol=https\nhost=github.com\n\n')
    fields = dict(line.split('=', 1) for line in creds.splitlines() if '=' in line)
    token = fields.get('password')
    if not token:
        raise RuntimeError('GitHub credential cache is empty')

    inputs = {
        'round': 'd4',
        'release_tag': 'd4-springboard',
        'release_name': 'D4: GPU ledger diagnostic',
        'kernel_repo': REPO,
        'wifi_stack': '1',
        'base_daily_url': BASE + 'raphael-xnu-d3-flash.img',
        'base_daily_sha': 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b',
        'base_gpu_url': BASE + 'raphael-xnu-d3-gpu-flash.img',
        'base_gpu_sha': D3_GPU,
        'base_flashboot_url': BASE + 'raphael-d3-flashboot.apfs',
        'base_flashboot_sha': '14485e7c5ce125ef516168445c2500ac04ca5e40a908c2aef516b2d92c24ba0c',
        'base_entry_shas': 'loader.efi=' + LOADER + '\nkernel=' + D3_KERNEL + '\nramdisk=' + VOLUME,
        'base_id_cmd': 'python3 tools/d4-kernel-id.py "$BASE_DAILY" > "$BASE_IDFILE"; python3 tools/d4-kernel-id.py "$BASE_GPU" --expect-loader-sha ' + LOADER + ' --expect-kernel-sha ' + D3_KERNEL + ' --expect-volume-sha ' + VOLUME + ' >> "$BASE_IDFILE"',
        'base_id_gates': 'grep -q "the kernel entry is R33" "$BASE_IDFILE" && grep -q "the kernel entry is D3" "$BASE_IDFILE"',
        'pre_gate_py': "import pathlib, sys\nb = pathlib.Path(sys.argv[1]).read_bytes()\nroot = pathlib.Path('xnu/files')\nbuilt = pathlib.Path('xnu-src/xnu-8796.121.2')\nfor path in ('iokit/Kernel/RedwoodGPU.cpp', 'bsd/dev/raphael_adreno.c', 'iokit/Kernel/MiniIOMobileFramebuffer.cpp'):\n    assert (root / path).read_bytes() == (built / path).read_bytes(), 'built source differs from overlay: ' + path\nsrc = (root / 'bsd/dev/raphael_adreno.c').read_bytes()\nassert src.count(b'#define CORE_GUARD_ADDRESS      0x00000000u') == 1, 'D3 guard address changed'\nassert src.count(b'#define CORE_GUARD_SIZE         0x200000u') == 1, 'D3 guard size changed'\nassert b.count(b'D4: the GPU ledger reports its candidates safely at last') == 1, 'D4 marker missing or duplicated'\nassert b'D3: the 3D core guard covers two megabytes for diagnosis' not in b and b'D1: the stalled command buffers read aloud, word by word' not in b, 'old marker present'\nassert b.count(b'R33: the kernel generation line') == 2\nassert b'adreno: gpu: dump:' in b\nprint('D4 pre-gate passed')",
        'kernel_gate_cmd': 'python3 tools/d4_kernel_gate.py --base-kernel "$BASE_KERNEL" --d4-kernel "$K" --report "$REL/${R}-kernel-gate.txt" | tee "$REL/${R}-kernel-gate-run.txt"',
        'flash_build_cmd': 'python3 tools/d4_flash_build.py --daily "$BASE_DAILY" --gpu "$BASE_GPU" --kernel "$K" --out-daily "$REL/raphael-xnu-${R}-flash.img" --out-gpu "$REL/raphael-xnu-${R}-gpu-flash.img" | tee "$REL/${R}-pack.txt"',
        'kernel_id_cmd': 'python3 tools/d4-kernel-id.py "$REL/raphael-xnu-${R}-flash.img" > "$IDFILE"; python3 tools/d4-kernel-id.py "$REL/raphael-xnu-${R}-gpu-flash.img" --expect-loader-sha ' + LOADER + ' --expect-kernel-sha "$KSHA" --expect-volume-sha ' + VOLUME + ' >> "$IDFILE"',
        'id_gates': 'test "$(grep -c "the kernel entry is R33" "$IDFILE")" = 1 && test "$(grep -c "the kernel entry is D4" "$IDFILE")" = 1 && grep -q "the cmdline is the R39 GPU form verbatim" "$IDFILE" && grep -q "the cmdline is the daily image WITHOUT" "$IDFILE"',
        'pre_release_cmd': 'cp base/flashboot.apfs "$REL/raphael-${R}-flashboot.apfs"; cp base/md1-root.apfs "$REL/${R}-md1-root.apfs"',
        'release_readme': 'D4 GPU ledger diagnostic; D3 is the rollback baseline. Experimental and not device-validated.',
        'release_body': 'D4 ledger diagnostic build; the D3 2 MiB guard remains unchanged.',
        'checksum_files': 'raphael-xnu-d4-flash.img raphael-xnu-d4-gpu-flash.img raphael-d4-flashboot.apfs d4-md1-root.apfs',
        'release_files': 'raphael-xnu-d4-flash.img\nraphael-xnu-d4-gpu-flash.img\nraphael-d4-flashboot.apfs\nd4-md1-root.apfs\nd4-kernel-gate.txt\nSHA256SUMS\nREADME.txt\ntoolchain-versions.txt',
    }
    data = json.dumps({'ref': 'main', 'inputs': inputs}).encode()
    req = urllib.request.Request('https://api.github.com/repos/' + REPO + '/actions/workflows/springboard.yml/dispatches', data=data, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json', 'User-Agent': 'hackios-d4'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 204:
            raise RuntimeError('unexpected dispatch response: %s' % response.status)
    print('D4 dispatch accepted for commit', head)


if __name__ == '__main__':
    main()
