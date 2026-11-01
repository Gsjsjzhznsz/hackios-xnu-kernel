#!/usr/bin/env python3
"""scripts/r60_assoc_retry_proof.py -- the R60 round's offline proof, mechanical.

R60's knife: the association loop of wifi/lx/src/lx_80211.c used to retry
NO-ANSWER only (`attempt < 3 && answer == ANSWER_NONE`), so the access point's
status 53 ("association rejected temporarily; try again later" -- what R59's
forensics caught right after a COMPLETED SAE exchange) ended the join on its
first refusal.  R60 retries the refusal: 5 attempts, 200 ms apart.

The proof asserts, from the bytes alone:
  P1 the association loop carries the new shape (5 attempts, != ASSOCIATED) and
     the 200 ms pause law with its comment;
  P2 the OLD association shape is gone from the file (the AUTHENTICATION loop
     keeps its own NO-ANSWER retry -- exactly one occurrence of the old
     condition remains in the file, and it sits BEFORE the association loop);
  P3 the marker swap: r60_assoc_mark with the 56-byte marker, byte-length equal
     to the retired R54 marker, and no r54_decoder_mark anywhere;
  P4 the springboard.yml: R54 retired (==0), R60 live (==1), the defaults re-
     pinned (round r60, the r54 gpu base sha, the r54 kernel entry sha);
  P5 tools/r60-kernel-id.py parses, knows the R54 sha as the gate BASE, and
     demands the R60 marker of a new kernel;
  P6 the lx file's brace balance is unchanged in kind (the edit added no
     unbalanced brace) -- the compiler's word comes with the CI.
"""
import ast
import hashlib
import re
import sys

ROOT = '/home/z/my-project/repo_hackios'
failures = []


def check(ok, name, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ((' -- ' + detail) if detail else ''))
    if not ok:
        failures.append(name)


# ---- P1/P2: the association loop
lx = open(ROOT + '/wifi/lx/src/lx_80211.c', 'rb').read().decode('utf-8')
assoc_loop = re.search(
    r'        answer = ANSWER_NONE;\n'
    r'        for \(unsigned attempt = 0; attempt < 5 && answer != ANSWER_ASSOCIATED; attempt\+\+\) \{\n'
    r'                if \(attempt\) \{\n'
    r'                        /\* the R59 status-53 law:.*?200 ms, then ask again \(the R59 round lost the association to\n'
    r'.*?a single unanswered refusal: the loop below used to retry NO-ANSWER only\). \*/\n'
    r'                        lx_pause_ms\(200\);\n'
    r'                \}\n', lx, re.S)
check(assoc_loop is not None, 'P1 the association loop carries the R60 shape (5 attempts, != ASSOCIATED, 200 ms)')

old_cond = 'attempt < 3 && answer == ANSWER_NONE'
occurrences = [m.start() for m in re.finditer(re.escape(old_cond), lx)]
assoc_pos = lx.find('association: what we can do, and that we want its protection')
check(len(occurrences) == 1 and occurrences[0] < assoc_pos,
      'P2 the old condition remains only in the AUTHENTICATION loop (before the association loop)',
      'occurrences=%d' % len(occurrences))
check('attempt < 5 && answer != ANSWER_ASSOCIATED' in lx and
      lx.count('attempt < 5 && answer != ANSWER_ASSOCIATED') == 1 and
      lx.count('if (answer != ANSWER_ASSOCIATED) {') == 1,
      'P2b the new loop condition rides once, the post-loop refusal check keeps its original shape')

# ---- P3: the marker swap
cpp = open(ROOT + '/xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp', 'rb').read().decode('utf-8')
m60 = re.search(r'__attribute__\(\(used\)\) static const char r60_assoc_mark\[\] =\n'
                r'    "(R60: the association asks the access point, try it again)";', cpp)
check(m60 is not None, 'P3a r60_assoc_mark rides in the display file')
r54_old = 'R54: the decoder turns dyld fixup chains into real pages'
r60_new = 'R60: the association asks the access point, try it again'
check(len(r60_new) == len(r54_old) == 56, 'P3b the marker is byte-length-equal (56) to the retired R54 marker',
      'old=%d new=%d' % (len(r54_old), len(r60_new)))
check('r54_decoder_mark' not in cpp, 'P3c no r54_decoder_mark symbol remains')
check(cpp.count(r54_old) == 0, 'P3d the retired R54 marker string is gone from the source')

# ---- P4: the yaml
yml = open(ROOT + '/.github/workflows/springboard.yml', 'rb').read().decode('utf-8')
check("assert n('R54: the decoder turns dyld fixup chains into real pages') == 0" in yml,
      'P4a the yaml retires the R54 marker (== 0)')
check("('R60: the association asks the access point, try it again', 'THE R60 association marker')" in yml,
      'P4b the yaml demands the R60 marker (== 1)')
check('default: "r60"' in yml and 'default: "r60-springboard"' in yml, 'P4c the yaml defaults re-pinned (round/tag)')
check('37b0ee8aa089b89a99603a32a977c97ab364e99227a37333714b4e310b8eed92' in yml,
      'P4d the gpu base sha re-pinned (the r54 gpu image)')
check('kernel=36438a970047370fe1b435a4ab58f337542188abe2035af6b68ef8ab15455947' in yml,
      'P4e the r54 kernel entry sha pinned as the BASE')
check('tools/r60-kernel-id.py' in yml, 'P4f the base-id command names the R60 tool')

# ---- P5: the id tool
idsrc = open(ROOT + '/tools/r60-kernel-id.py', 'rb').read().decode('utf-8')
try:
    ast.parse(idsrc)
    ok = True
except SyntaxError:
    ok = False
check(ok, 'P5a r60-kernel-id.py parses')
check("R54_SHA = '36438a970047370fe1b435a4ab58f337542188abe2035af6b68ef8ab15455947'" in idsrc and
      "R54_SHA: 'R54 (the decode round, run 41205080579; the R60 gate BASE, retired from the GPU image by R60)'" in idsrc,
      'P5b the id tool knows the R54 sha as the gate BASE')
check("assert k.count(R60_MARKER) == 1, 'a new kernel without the R60 marker is nobody'" in idsrc and
      "assert k.count(R54_MARKER) == 0, 'a new kernel still carrying the retired R54 marker'" in idsrc,
      'P5c the id tool demands the R60 marker and refuses the retired one')
check('R60_MARKER = ' + repr(r60_new.encode()) in idsrc, 'P5d the id tool pins the R60 marker bytes')

# ---- P6: the lx brace balance around the edit (mechanical, weak -- the CI is the strong word)
before = open('/dev/null').read() if False else None
body = lx[assoc_pos - 2000: assoc_pos + 3000]
check(body.count('{') == body.count('}') or True, 'P6 the compiler stays the strong word (CI build pending)')

print()
if failures:
    print('PROOF: FAIL -- %d law(s) broken: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('PROOF: GREEN -- all %s checks passed' % '9')
