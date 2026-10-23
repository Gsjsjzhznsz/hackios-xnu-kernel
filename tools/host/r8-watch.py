#!/usr/bin/env python3
"""Round watcher: poll a GitHub Actions run; once it succeeds and its release
exists, download the named assets with size+sha256 verification, plus the
round's FLASH doc from docs/.

Per the R8 rule (docs/WORKLOG.md) this lives in the repo: a lost session must
be able to pick a round back up from the repository alone.

Usage: python3 tools/host/r8-watch.py RUN_ID TAG DEST_DIR [max_wait_seconds]
Token: GITHUB_TOKEN env first, else whatever is embedded in this checkout's
       git remote URL (works for token-embedded https clones).

Exit codes: 0 delivered; 3 still running at max_wait; 1 failure anywhere.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request


def token():
    env = os.environ.get('GITHUB_TOKEN')
    if env:
        return env
    url = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                         capture_output=True, text=True).stdout
    m = re.search(r'https://[^:]+:([^@]+)@', url)
    if not m:
        sys.exit('no GITHUB_TOKEN and no token in the remote URL')
    return m.group(1)


def gh(url, tok, accept='application/vnd.github+json', binary=False):
    req = urllib.request.Request(url, headers={'Authorization': f'token {tok}',
                                               'Accept': accept})
    return urllib.request.urlopen(req, timeout=300).read()


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    run_id, tag, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    max_wait = int(sys.argv[4]) if len(sys.argv) > 4 else 3600
    repo = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                          capture_output=True, text=True).stdout
    repo = re.search(r'github\.com[:/]([^/]+/[^/.]+)', repo).group(1)
    tok = token()
    api = f'https://api.github.com/repos/{repo}'

    deadline = time.time() + max_wait
    while True:
        run = json.loads(gh(f'{api}/actions/runs/{run_id}', tok))
        print(time.strftime('%H:%M:%S'), run['status'], run.get('conclusion') or '',
              f'https://github.com/{repo}/actions/runs/{run_id}', flush=True)
        if run['status'] == 'completed':
            if run.get('conclusion') != 'success':
                sys.exit(f'RUN FAILED: {run.get("conclusion")}')
            break
        if time.time() > deadline:
            sys.exit(3)
        time.sleep(45)

    rel = json.loads(gh(f'{api}/releases/tags/{tag}', tok))
    assets = {a['name']: a for a in rel['assets']}
    print('release', rel['tag_name'], '| assets:', {n: a['size'] for n, a in assets.items()})

    m = re.match(r'r(\d+)', tag)
    rnd = m.group(1) if m else '?'
    want = [f'raphael-xnu-r{rnd}-flash.img', 'SHA256SUMS', 'README.txt']
    os.makedirs(dest, exist_ok=True)

    sums = {}
    for name in want:
        a = assets.get(name)
        if a is None:
            sys.exit(f'MISSING asset {name}')
        data = gh(a['url'], tok, accept='application/octet-stream')
        if len(data) != a['size']:
            sys.exit(f'SIZE MISMATCH {name}: got {len(data):,} want {a["size"]:,}')
        path = os.path.join(dest, name)
        open(path, 'wb').write(data)
        print(f'{name}: {len(data):,} B -> {path}')
        if name == 'SHA256SUMS':
            sums = {f.split()[0]: f.split(None, 1)[1].strip()
                    for f in data.decode().splitlines() if f.strip()}

    img = os.path.join(dest, f'raphael-xnu-r{rnd}-flash.img')
    got = hashlib.sha256(open(img, 'rb').read()).hexdigest()
    exp = next((h for h, f in sums.items() if f'raphael-xnu-r{rnd}-flash' in f), None)
    print(f'flash img sha256 {got[:16]} | release says {(exp or "?")[:16]}')
    if exp and got != exp:
        sys.exit('SHA MISMATCH')

    # the round's FLASH doc and the human README, under round-tagged names
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    flash = os.path.join(here, 'docs', f'FLASH-R{rnd}.md')
    if os.path.isfile(flash):
        open(os.path.join(dest, f'FLASH-R{rnd}.md'), 'wb').write(open(flash, 'rb').read())
        print(f'FLASH-R{rnd}.md copied')
    if os.path.isfile(os.path.join(dest, 'README.txt')):
        os.rename(os.path.join(dest, 'README.txt'), os.path.join(dest, f'R{rnd}-README.txt'))
    print('DELIVERED')


if __name__ == '__main__':
    main()
