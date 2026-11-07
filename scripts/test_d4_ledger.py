#!/usr/bin/env python3
"""Compile current D4 ledger fragments with host stubs; not a kernel test."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'xnu/files/iokit/Kernel/RedwoodGPU.cpp'


def between(source, start, end):
    if source.count(start) != 1 or source.count(end) != 1:
        raise RuntimeError('source layout changed; cannot uniquely extract ' + start)
    return source[source.index(start):source.index(end)]


def make_program(source):
    ledger = between(source, '#define GPU_STALL_RING', '/* Wait for the 3D core')
    dump = between(source, 'extern "C" void\nredwood_gpu_dump_stalled_ib', '\nbool\nMiniIOSurfaceDrawn')
    recording = between(source, '\t/* Successful submissions only.', '\tif (gpu_log && request->fence != 0)')
    submit_types = between(source, 'struct msm_gem_submit_cmd {', 'struct msm_wait_fence {')
    buffer_type = between(source, 'struct gpu_buffer {', '\n/*\n * A buffer given back')
    defines = []
    for name in ('MSM_SUBMIT_CMD_BUF', 'GPU_BUFFERS', 'GPU_SUBMIT_CMDS'):
        lines = [line for line in source.splitlines() if line.startswith('#define ' + name + ' ')]
        if len(lines) != 1:
            raise RuntimeError('missing or ambiguous definition: ' + name)
        defines.append(lines[0])
    return PRELUDE + '\n'.join(defines) + '\n' + submit_types + buffer_type + ledger + dump + RECORD_PREFIX + recording + RECORD_SUFFIX + TESTS


PRELUDE = r'''
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <sys/mman.h>
#include <unistd.h>
struct IOMemoryDescriptor {};
struct MiniIOSurface {};
struct IOLock { bool held; bool fail; int tries; int unlocks; };
static IOLock lock_state = {};
static IOLock *gGPULock = &lock_state;
static bool IOLockTryLock(IOLock *lock) {
    ++lock->tries;
    if (lock->held || lock->fail) return false;
    lock->held = true; return true;
}
static void IOLockUnlock(IOLock *lock) {
    if (!lock->held) std::abort();
    lock->held = false; ++lock->unlocks;
}
static std::string output;
static int capture(const char *format, ...) {
    char text[2048]; va_list args; va_start(args, format);
    int n = vsnprintf(text, sizeof(text), format, args); va_end(args);
    if (n < 0 || (size_t)n >= sizeof(text)) std::abort();
    output.append(text, n); return n;
}
#define printf capture
'''
RECORD_PREFIX = r'''
static gpu_buffer fBuffers[GPU_BUFFERS] = {};
static void record(msm_gem_submit *request, msm_gem_submit_bo *bos,
                   msm_gem_submit_cmd *cmds, uint32_t count) {
'''
RECORD_SUFFIX = '\n}\n'

TESTS = r'''
static void check(bool condition, const char *message) {
    if (!condition) { std::fprintf(stderr, "FAIL: %s\n%s", message, output.c_str()); std::exit(1); }
}
static void contains(const char *text) { check(output.find(text) != std::string::npos, text); }
static void lacks(const char *text) { check(output.find(text) == std::string::npos, text); }
static size_t occurrences(const char *text) {
    size_t count = 0, at = 0; const std::string needle(text);
    while ((at = output.find(needle, at)) != std::string::npos) { ++count; at += needle.size(); }
    return count;
}
static void init_buffer(uint32_t handle, uint64_t address, uint64_t size) {
    fBuffers[handle-1].memory = reinterpret_cast<IOMemoryDescriptor *>(1);
    fBuffers[handle-1].kernel = reinterpret_cast<uint8_t *>(1);
    fBuffers[handle-1].address = address; fBuffers[handle-1].size = size;
}
static void simple(uint32_t fence, uint64_t base = 0x1000, uint32_t words = 4) {
    init_buffer(1, base, 0x100);
    msm_gem_submit req = {}; req.fence = fence; req.nr_bos = 1; req.nr_cmds = 1;
    msm_gem_submit_bo bo = {}; bo.handle = 1;
    msm_gem_submit_cmd cmd = {}; cmd.type = MSM_SUBMIT_CMD_BUF; cmd.size = words * 4;
    record(&req, &bo, &cmd, 1);
}
int main(int argc, char **argv) {
    check(argc == 2, "case argument"); std::string test(argv[1]);
    if (test == "empty") {
        redwood_gpu_dump_stalled_ib(0x1234, 0);
        contains("0 records of at most"); contains("0 candidate submissions");
        contains("exact matches 0, printed 0, omitted from output 0");
        contains("IB matches 0, printed 0, omitted from output 0");
        contains("BO matches 0, printed 0, omitted from output 0");
    } else if (test == "exact") {
        simple(7); redwood_gpu_dump_stalled_ib(0x1000, 4);
        contains("candidate fence 7: exact cmd index 0"); contains("candidate fence 7: BO range index 0");
        contains("1 candidate submissions"); contains("exact matches 1, printed 1, omitted from output 0");
        contains("IB matches 0, printed 0, omitted from output 0");
        contains("BO matches 1, printed 1, omitted from output 0");
    } else if (test == "inside") {
        simple(8); redwood_gpu_dump_stalled_ib(0x100c, 1);
        contains("candidate fence 8: range cmd index 0"); contains("candidate fence 8: BO range index 0");
    } else if (test == "end") {
        simple(9); redwood_gpu_dump_stalled_ib(0x1010, 0);
        lacks("cmd index"); contains("candidate fence 9: BO range index 0");
    } else if (test == "overflow") {
        simple(10, UINT64_MAX - 7, 4); redwood_gpu_dump_stalled_ib(1, 1);
        contains("0 candidate submissions"); contains("exact matches 0"); contains("IB matches 0"); contains("BO matches 0");
    } else if (test == "lock") {
        simple(11); lock_state.fail = true;
        redwood_gpu_dump_stalled_ib(0x1000, 1);
        contains("ledger skipped"); check(lock_state.unlocks == 0, "no unlock on failed try");
        output.clear(); lock_state.fail = false;
        redwood_gpu_dump_stalled_ib(0x1000, 1); contains("candidate fence 11: exact");
        check(lock_state.unlocks == 1 && !lock_state.held, "successful dump unlocks");
        output.clear(); redwood_gpu_dump_stalled_ib(0x1000, 1);
        check(output.empty() && lock_state.unlocks == 2, "one-shot gate unlocks silently");
    } else if (test == "null-lock") {
        gGPULock = nullptr; redwood_gpu_dump_stalled_ib(0, 0); contains("ledger skipped");
        gGPULock = &lock_state; output.clear(); redwood_gpu_dump_stalled_ib(0, 0);
        contains("0 records of at most");
    } else if (test == "reuse") {
        simple(1); for (uint32_t i = 2; i <= GPU_STALL_RING + 1; ++i) simple(i, 0x2000);
        check(gGPUStallAt == 1, "ring wraps");
        redwood_gpu_dump_stalled_ib(0x1000, 1); contains("0 candidate submissions");
        lacks("candidate fence 1:"); contains("48 records of at most 48");
    } else if (test == "stale") {
        init_buffer(1, 0x1000, 0x100); init_buffer(2, 0x9000, 0x100);
        msm_gem_submit req = {}; req.fence = 1; req.nr_bos = req.nr_cmds = 2;
        msm_gem_submit_bo bos[2] = {}; bos[0].handle = 1; bos[1].handle = 2;
        msm_gem_submit_cmd cmds[2] = {}; cmds[0].type = cmds[1].type = MSM_SUBMIT_CMD_BUF;
        cmds[0].size = cmds[1].size = 4; cmds[1].submit_idx = 1;
        record(&req, bos, cmds, 2);
        for (uint32_t i = 2; i <= GPU_STALL_RING + 1; ++i) simple(i, 0x2000);
        check(gGPUStall[0].ibs == 1 && gGPUStall[0].bos == 1, "reused slot counts");
        redwood_gpu_dump_stalled_ib(0x9000, 1); contains("0 candidate submissions");
    } else if (test == "indices") {
        init_buffer(1, 0x1000, 0x100); init_buffer(2, 0x2000, 0x100);
        fBuffers[1].memory = nullptr;
        msm_gem_submit req = {}; req.fence = 3; req.nr_bos = 2; req.nr_cmds = 2;
        msm_gem_submit_bo bos[2] = {}; bos[0].handle = 1; bos[1].handle = 2;
        msm_gem_submit_cmd cmds[2] = {}; cmds[1].type = MSM_SUBMIT_CMD_BUF;
        cmds[1].submit_offset = 8; cmds[1].size = 8;
        record(&req, bos, cmds, 1);
        check(gGPUStall[0].ib[0].index == 1 && gGPUStall[0].bo_invalid == 1, "source indices and invalid free handle");
        redwood_gpu_dump_stalled_ib(0x1008, 2); contains("candidate fence 3: exact cmd index 1");
    } else if (test == "failed") {
        simple(0); check(gGPUStallAt == 0, "zero fence must not advance ring");
        redwood_gpu_dump_stalled_ib(0x1000, 1); contains("0 records of at most");
    } else if (test == "coverage") {
        const uint32_t n = GPU_STALL_BOS + 3;
        std::vector<msm_gem_submit_bo> bos(n); std::vector<msm_gem_submit_cmd> cmds(GPU_STALL_IBS + 1);
        for (uint32_t i = 0; i < n; ++i) { bos[i].handle = i + 1; init_buffer(i + 1, 0x1000 + i*0x100, 0x80); }
        bos[n-2].handle = 0; bos[n-1].handle = GPU_BUFFERS + 1;
        for (auto &cmd : cmds) { cmd.type = MSM_SUBMIT_CMD_BUF; cmd.size = 4; }
        msm_gem_submit req = {}; req.fence = 42; req.nr_bos = n; req.nr_cmds = cmds.size();
        record(&req, bos.data(), cmds.data(), cmds.size());
        check(gGPUStall[0].ibs == GPU_STALL_IBS && gGPUStall[0].ib_all == GPU_STALL_IBS+1, "command truncation");
        check(gGPUStall[0].bos == GPU_STALL_BOS && gGPUStall[0].bo_invalid == 2 && gGPUStall[0].bo_omitted == 1, "BO coverage");
        redwood_gpu_dump_stalled_ib(0x1000, 1);
        contains("1 incomplete records (1 with truncated commands), 2 invalid BOs, 1 BOs omitted by cap");
        contains("truncated yes");
    } else if (test == "cap") {
        for (uint32_t i = 1; i <= GPU_STALL_RING; ++i) simple(i);
        redwood_gpu_dump_stalled_ib(0x1000, 1);
        check(occurrences("candidate fence ") == GPU_STALL_PRINT, "candidate print cap");
        contains("48 candidate submissions"); contains("exact matches 48, printed 32, omitted from output 16");
        contains("IB matches 0, printed 0, omitted from output 0");
        contains("BO matches 48, printed 0, omitted from output 48");
        check(output.find("candidate fence 48:") < output.find("candidate fence 47:"), "newest first");
        lacks("candidate fence 1:");
    } else if (test == "priority") {
        // A newer containing match must not displace an older exact match.
        simple(1, 0x1004); for (uint32_t i = 2; i <= GPU_STALL_RING; ++i) simple(i, 0x1000);
        redwood_gpu_dump_stalled_ib(0x1004, 1);
        contains("candidate fence 1: exact cmd index 0");
        contains("exact matches 1, printed 1, omitted from output 0");
        contains("IB matches 47, printed 31, omitted from output 16");
        contains("BO matches 48, printed 0, omitted from output 48");
        check(output.find("candidate fence 1: exact") < output.find("candidate fence 48: range"), "match priority");
    } else if (test == "inaccessible") {
        long page = sysconf(_SC_PAGESIZE); check(page > 0, "page size");
        void *p = mmap(nullptr, page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        check(p != MAP_FAILED, "mmap");
        init_buffer(1, reinterpret_cast<uintptr_t>(p), page);
        fBuffers[0].kernel = static_cast<uint8_t *>(p);
        msm_gem_submit req = {}; req.fence = 50; req.nr_bos = req.nr_cmds = 1;
        msm_gem_submit_bo bo = {}; bo.handle = 1;
        msm_gem_submit_cmd cmd = {}; cmd.type = MSM_SUBMIT_CMD_BUF; cmd.size = 16;
        record(&req, &bo, &cmd, 1);
        redwood_gpu_dump_stalled_ib(reinterpret_cast<uintptr_t>(p), 4);
        contains("candidate fence 50: exact"); contains("historical command words are not read");
        check(munmap(p, page) == 0, "munmap");
    } else { check(false, "unknown case"); }
    check(!lock_state.held, "lock released");
    std::puts("PASS"); return 0;
}
'''


def main():
    cases = ('empty', 'exact', 'inside', 'end', 'overflow', 'lock', 'null-lock',
             'reuse', 'stale', 'indices', 'failed', 'coverage', 'cap', 'priority', 'inaccessible')
    program = make_program(SOURCE.read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='d4-ledger-test-') as directory:
        cpp = Path(directory) / 'ledger.cpp'
        exe = Path(directory) / 'ledger'
        cpp.write_text(program, encoding='utf-8')
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-O0', '-g',
                        '-Wall', '-Wextra', str(cpp), '-o', str(exe)], check=True)
        for case in cases:
            subprocess.run([str(exe), case], check=True)
            print('PASS:', case, flush=True)
    print('Host fragments only: no kernel build, real locking, hardware, or end-to-end submit validation.')
    print('Command truncation is synthetic: the current submit limit normally prevents it.')


if __name__ == '__main__':
    main()
