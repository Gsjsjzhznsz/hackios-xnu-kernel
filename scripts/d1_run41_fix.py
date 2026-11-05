#!/usr/bin/env python3
"""d1_run41_fix.py -- the run #41 autopsy fix: the six GPU_STALL_* #defines
must precede the struct that uses them as an array bound.

CI named it (run 41866501957, step 3): RedwoodGPU.cpp:253:29 'use of undeclared
identifier GPU_STALL_IBS' (and GPU_STALL_BOS on 254) -- the offline proof
policed the constants' VALUES but not their ORDER; C wants the bound before the
bound user.  The cure is a byte-exact move of the #define block from after
struct gpu_stall_record to before struct gpu_stall_range; nothing else changes.
"""
import pathlib

F = pathlib.Path('/home/z/my-project/repo_hackios/xnu/files/iokit/Kernel/RedwoodGPU.cpp')
b = F.read_bytes()

first = b.find(b'#define GPU_STALL_RING')
hit2 = b.find(b'#define GPU_STALL_RING', first + 1)
assert first > 0 and hit2 == -1, 'the RING define appears x%d' % (1 + (hit2 > 0))
endh = b.find(b'#define GPU_STALL_HUNT_HITS')
assert endh > first
eol = b.find(b'\n', endh)
assert eol > 0
block = b[first:eol + 1]
assert block.count(b'\n') == 6, 'the block is %d lines, want 6' % block.count(b'\n')
assert b.count(block) == 1, 'the block is not byte-unique'
assert b'GPU_STALL_HUNT_HITS     6' in block

without = b.replace(block, b'')
tail = without[first:first + 64]
assert b'static struct gpu_stall_record gGPUStall' in without[first:first + 120], \
    'after the removal the defines are not followed by the ledger ring: %r' % tail[:40]

anchor = b'struct gpu_stall_range {'
assert without.count(anchor) == 1
moved = without.replace(anchor, block + anchor)
F.write_bytes(moved)

nb = F.read_bytes()
assert nb.find(b'#define GPU_STALL_RING') < nb.find(b'struct gpu_stall_range'), 'order not fixed'
assert nb.count(b'#define GPU_STALL_RING') == 1 and nb.count(b'GPU_STALL_HUNT_HITS') == 2, 'count drift'
assert nb.count(b'{') - nb.count(b'}') == b.count(b'{') - b.count(b'}'), 'brace drift'
print('moved the 6-#define block before struct gpu_stall_range: bytes %d -> %d' % (len(b), len(nb)))
