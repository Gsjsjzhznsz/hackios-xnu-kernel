#!/usr/bin/env python3
"""scripts/r61_evidence_proof.py -- the R61 round's offline proof, mechanical.

R61's knife (the evidence round): the R60 session's association answer carried
67 bytes of elements nobody could read, the summary line lied about the ask
count ("three requests" -- the loop is five), and no ask announced itself.
R61: the answer's elements are read out in hex (sixteen to a line), the honest
ask count rides the summary, and each ask prints its number.

The proof asserts, from the bytes alone:
  P1 the association loop: the R60 retry shape intact (5 attempts, !=
     ASSOCIATED, 200 ms) + the per-ask print + the honest "after %u asks" +
     the hex-dump block (16 per line, ELEMENTS_MOST-capped);
  P2 the stale "three requests" string is gone from the ASSOCIATION tell and
     STILL rides in the AUTHENTICATION tell (that loop IS three);
  P3 the marker swap: r61_assoc_mark with the 56-byte marker, byte-length
     equal to the retired R60 marker, and no r60_assoc_mark anywhere;
  P4 the springboard.yml: R60 retired (==0), R61 live (==1), the bases = the
     r60 release (the gpu 0a0683f1, the base kernel entry e0570b2a), the r61
     tools named, the release env in r61 names;
  P5 the three r61 tools parse; the id tool knows the R60 sha as the gate BASE
     and demands the R61 marker of a new kernel; the gate keeps the R60
     retired pair (new x0 / base x1); the flash_build asserts the same;
  P6 the lx file's brace balance is unchanged in kind -- the compiler's word
     comes with the CI.
"""
import ast
import re

ROOT = '/home/z/my-project/repo_hackios'
failures = []


def check(ok, name, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ((' -- ' + detail) if detail else ''))
    if not ok:
        failures.append(name)


# ---- P1: the association loop
lx = open(ROOT + '/wifi/lx/src/lx_80211.c', 'rb').read().decode('utf-8')
assoc_pos = lx.find('association: what we can do, and that we want its protection')
loop = lx[assoc_pos:assoc_pos + 6000]
check('attempt < 5 && answer != ANSWER_ASSOCIATED' in loop, 'P1a the R60 retry shape intact')
check('lx_pause_ms(200);' in loop, 'P1b the 200 ms settle law intact')
check(re.search(r'lx_tell\("lx: the association asks, attempt %u of 5\\n", attempt \+ 1\);', loop) is not None,
      'P1c each ask announces itself (attempt N of 5)')
check('after %u asks' in loop and 'radio->answer_status, radio->aid, radio->answer_length, attempt);' in loop,
      'P1d the honest ask count rides the summary')
check(re.search(r'for \(unsigned i = 0; i < radio->answer_length && i < ELEMENTS_MOST; i \+= 16\)', loop) is not None
      and 'char hex[49];' in loop
      and '"0123456789abcdef"[radio->answer_elements[i + j] >> 4]' in loop
      and 'lx_tell("lx: the association answer, bytes %u..%u: %s\\n", i, i + m - 1, hex);' in loop,
      'P1e the hex-dump block (16 per line, 49 B stack, ELEMENTS_MOST-capped)')

# ---- P2: the stale label's two-sided law
assoc_tell = re.search(r'lx_tell\("lx: ASSOCIATION: %s \(status %u\);.*?\\n", answer ==\s*'
                       r'ANSWER_ASSOCIATED \? "THE ACCESS POINT ACCEPTS" : answer == ANSWER_NONE \? "([^"]*)"',
                       lx, re.S)
check(assoc_tell is not None and 'three' not in assoc_tell.group(1),
      'P2a the ASSOCIATION tell carries no stale "three"',
      'NO-ANSWER branch: %r' % (assoc_tell.group(1) if assoc_tell else None))
check(lx.count('to three ') == 1 and lx.find('to three ') < assoc_pos,
      'P2b the stale label remains exactly once, in the AUTHENTICATION tell (that loop IS three)')

# ---- P3: the marker swap
cpp = open(ROOT + '/xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp', 'rb').read().decode('utf-8')
m61 = re.search(r'__attribute__\(\(used\)\) static const char r61_assoc_mark\[\] =\n'
                r'    "(R61: the association answer read aloud in hex, all bytes)";', cpp)
check(m61 is not None, 'P3a r61_assoc_mark rides in the display file')
r60_old = 'R60: the association asks the access point, try it again'
r61_new = 'R61: the association answer read aloud in hex, all bytes'
check(len(r61_new) == len(r60_old) == 56, 'P3b the marker is byte-length-equal (56) to the retired R60 marker',
      'old=%d new=%d' % (len(r60_old), len(r61_new)))
check('r60_assoc_mark' not in cpp, 'P3c no r60_assoc_mark symbol remains')
check(cpp.count(r60_old) == 0, 'P3d the retired R60 marker string is gone from the source')
check(lx.count(r61_new) == 0 and lx.count(r60_old) == 0, 'P3e the marker lives only in the display file (the lx tells use their own words)')

# ---- P4: the yaml
yml = open(ROOT + '/.github/workflows/springboard.yml', 'rb').read().decode('utf-8')
check("assert n('R61: the association answer read aloud in hex, all bytes') == 0" not in yml and
      "assert n('R60: the association asks the access point, try it again') == 0" in yml,
      "P4a the yaml retires the R60 marker (== 0)")
check("('R61: the association answer read aloud in hex, all bytes', 'THE R61 association marker')" in yml,
      "P4b the yaml demands the R61 marker (== 1)")
check('default: "r61"' in yml and 'default: "r61-springboard"' in yml, 'P4c the yaml defaults re-pinned (round/tag)')
check('0a0683f1e7771dc99c23446afad42c255b5001137210685a888fd3ab69f296fe' in yml,
      'P4d the gpu base sha re-pinned (the r60 gpu image)')
check('kernel=e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7' in yml,
      'P4e the r60 kernel entry sha pinned as the BASE')
check('tools/r61-kernel-id.py' in yml and 'tools/r61_kernel_gate.py' in yml and 'tools/r61_flash_build.py' in yml,
      'P4f the commands name the R61 tools')
check('the kernel entry is R60 (the association-retry round' in yml,
      'P4g the base id verdict check names the R60 entry')
release_env = '\n'.join(l for l in yml.splitlines() if 'releases/download/' not in l)
check(all(x not in release_env for x in ['raphael-xnu-r60-flash.img', 'r60-md1-root.apfs', 'r60-kernel-gate.txt']) and
      all(x in yml for x in ['raphael-xnu-r61-gpu-flash.img', 'r61-md1-root.apfs', 'r61-kernel-gate.txt']),
      'P4h the release env carries the r61 asset names')

# ---- P5: the r61 tools
for tool in ('tools/r61_flash_build.py', 'tools/r61_kernel_gate.py', 'tools/r61-kernel-id.py'):
    try:
        ast.parse(open(ROOT + '/' + tool).read())
        ok = True
    except SyntaxError:
        ok = False
    check(ok, f'P5 {tool} parses')
id_src = open(ROOT + '/tools/r61-kernel-id.py').read()
check("R60_SHA = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'" in id_src and
      id_src.count('R61_MARKER = ') == 1 and id_src.count('R60_MARKER = ') == 1,
      'P5b the id tool knows the R60 BASE and carries exactly one of each marker const')
gate = open(ROOT + '/tools/r61_kernel_gate.py').read()
check('assert new.count(R60_MARKER) == 0' in gate and 'assert old.count(R60_MARKER) == 1' in gate and
      gate.count('R61_MARKER = ') == 1 and gate.count('R60_MARKER = ') == 1,
      'P5c the gate keeps the R60 retired pair (new x0 / base x1) and one of each const')
fb = open(ROOT + '/tools/r61_flash_build.py').read()
check('assert kernel.count(R61_MARKER) == 1' in fb and 'assert kernel.count(R60_MARKER) == 0' in fb and
      "R60_GPU = '0a0683f1e7771dc99c23446afad42c255b5001137210685a888fd3ab69f296fe'" in fb and
      "R60_KERNEL = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'" in fb,
      'P5d the flash_build pins the r60 base and demands the R61 marker of the new kernel')

# ---- P6: the brace balance of the lx edit (the diff is brace-neutral: the whole-file
# naive count is poisoned by braces inside string literals, so the law is on the DIFF)
import subprocess
diff = subprocess.run(['git', '-C', ROOT, 'diff', '--', 'wifi/lx/src/lx_80211.c'],
                      capture_output=True, text=True).stdout
add = sum(l.count('{') - l.count('}') for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))
rem = sum(l.count('{') - l.count('}') for l in diff.splitlines() if l.startswith('-') and not l.startswith('---'))
check(add == rem, 'P6 the lx edit is brace-neutral (added %d, removed %d; the compiler signs with the CI)' % (add, rem))

print()
if failures:
    print('PROOF: FAIL -- %d check(s): %s' % (len(failures), ', '.join(failures)))
    raise SystemExit(1)
print('PROOF: GREEN -- all checks passed')
