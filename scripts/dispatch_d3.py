#!/usr/bin/env python3
"""Dispatch D3 CI at the current committed and pushed main revision."""
import json
import subprocess
import urllib.request

REPO = 'Gsjsjzhznsz/hackios-xnu-kernel'
BASE = 'https://github.com/' + REPO + '/releases/download/d2-springboard/'
D2_GPU = '8f44f076cf822c49d604159b3ef6fbe3bfa14af5b962449edd29eba5c86b9c61'
D2_KERNEL = '82a3e023b39476066a1c02c5d052412dc734eecf3f5fe954175ca044b31d9d0a'
LOADER = '4bf828e425e4b59d3668461ff89ef290dfbf3215cceec903f1662255fe45a3db'
VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'
MARKER = 'D3: the 3D core guard covers two megabytes for diagnosis'
OLD_MARKER = 'D1: the stalled command buffers read aloud, word by word'


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
        'round': 'd3',
        'release_tag': 'd3-springboard',
        'release_name': 'D3: 2 MiB GPU guard diagnostic',
        'kernel_repo': REPO,
        'wifi_stack': '1',
        'base_daily_url': BASE + 'raphael-xnu-d2-flash.img',
        'base_daily_sha': 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b',
        'base_gpu_url': BASE + 'raphael-xnu-d2-gpu-flash.img',
        'base_gpu_sha': D2_GPU,
        'base_flashboot_url': BASE + 'raphael-d2-flashboot.apfs',
        'base_flashboot_sha': '14485e7c5ce125ef516168445c2500ac04ca5e40a908c2aef516b2d92c24ba0c',
        'base_entry_shas': 'loader.efi=' + LOADER + '\nkernel=' + D2_KERNEL + '\nramdisk=' + VOLUME,
        'base_id_cmd': 'python3 tools/d3-kernel-id.py "$BASE_DAILY" > "$BASE_IDFILE"; python3 tools/d3-kernel-id.py "$BASE_GPU" --expect-loader-sha ' + LOADER + ' --expect-kernel-sha ' + D2_KERNEL + ' --expect-volume-sha ' + VOLUME + ' >> "$BASE_IDFILE"',
        'base_id_gates': 'grep -q "the kernel entry is R33" "$BASE_IDFILE" && grep -q "the kernel entry is D2" "$BASE_IDFILE"',
        'pre_gate_py': 'import pathlib, sys\nb = open(sys.argv[1], "rb").read()\nsrc = pathlib.Path("xnu/files/bsd/dev/raphael_adreno.c").read_bytes()\nbuilt_src = pathlib.Path("xnu-src/xnu-8796.121.2/bsd/dev/raphael_adreno.c").read_bytes()\nassert src == built_src, "built driver differs from pinned overlay"\nassert src.count(b"#define CORE_GUARD_SIZE         0x200000u") == 1, "D3 guard size missing"\nassert b.count(' + repr(MARKER.encode()) + ') == 1\nassert ' + repr(OLD_MARKER.encode()) + ' not in b\nassert b.count(b"R33: the kernel generation line") == 2\nassert b"adreno: gpu: dump:" in b\nprint("D3 pre-gate passed")',
        'kernel_gate_cmd': 'python3 tools/d3_kernel_gate.py --base-kernel "$BASE_KERNEL" --d2-kernel "$K" --report "$REL/${R}-kernel-gate.txt" | tee "$REL/${R}-kernel-gate-run.txt"',
        'flash_build_cmd': 'python3 tools/d3_flash_build.py --daily "$BASE_DAILY" --gpu "$BASE_GPU" --kernel "$K" --out-daily "$REL/raphael-xnu-${R}-flash.img" --out-gpu "$REL/raphael-xnu-${R}-gpu-flash.img" | tee "$REL/${R}-pack.txt"',
        'kernel_id_cmd': 'python3 tools/d3-kernel-id.py "$REL/raphael-xnu-${R}-flash.img" > "$IDFILE"; python3 tools/d3-kernel-id.py "$REL/raphael-xnu-${R}-gpu-flash.img" --expect-loader-sha ' + LOADER + ' --expect-kernel-sha "$KSHA" --expect-volume-sha ' + VOLUME + ' >> "$IDFILE"',
        'id_gates': 'test "$(grep -c "the kernel entry is R33" "$IDFILE")" = 1 && test "$(grep -c "the kernel entry is D3" "$IDFILE")" = 1 && grep -q "the cmdline is the R39 GPU form verbatim" "$IDFILE" && grep -q "the cmdline is the daily image WITHOUT" "$IDFILE"',
        'pre_release_cmd': 'cp base/flashboot.apfs "$REL/raphael-${R}-flashboot.apfs"; cp base/md1-root.apfs "$REL/${R}-md1-root.apfs"',
        'release_readme': 'D3 diagnostic: 2 MiB writable GPU guard; D2 is the rollback baseline. This build is experimental and has not been device-validated.',
        'release_body': 'D3 diagnostic build. GPU guard expands from 16 KiB to 2 MiB; the D2 fault and its cause remain under investigation.',
        'checksum_files': 'raphael-xnu-d3-flash.img raphael-xnu-d3-gpu-flash.img raphael-d3-flashboot.apfs d3-md1-root.apfs',
        'release_files': 'raphael-xnu-d3-flash.img\nraphael-xnu-d3-gpu-flash.img\nraphael-d3-flashboot.apfs\nd3-md1-root.apfs\nd3-kernel-gate.txt\nSHA256SUMS\nREADME.txt\ntoolchain-versions.txt',
    }
    data = json.dumps({'ref': 'main', 'inputs': inputs}).encode()
    req = urllib.request.Request('https://api.github.com/repos/' + REPO + '/actions/workflows/springboard.yml/dispatches', data=data, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json', 'User-Agent': 'hackios-d3'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 204:
            raise RuntimeError('unexpected dispatch response: %s' % response.status)
    print('D3 dispatch accepted for commit', head)


if __name__ == '__main__':
    main()
