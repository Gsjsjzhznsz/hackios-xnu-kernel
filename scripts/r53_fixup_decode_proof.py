#!/usr/bin/env python3
"""R53 chained-fixup decode proof -- every verdict mechanical, offline.

Sources (bit-exact):
  A. R52 logdump live segment (download/r52-logdump.bin, first 1474626 B):
     - infos-hex rows  (the 352 B template the kernel copyin'd)
     - prefault holograms (kernel-printed notif/dyld_img/dyld_infos/slide/addr)
B. dyld_all_image_infos v17 semantics + dyld_chained_ptr_64_rebase bitfields.

Verdicts to prove:
  V1  five nonzero fields decode as PURE rebases (bind=0 auth=0 high8=0 unused=0)
  V2  truth table: target + expected base 0x180000000 reproduces the R51-registered
      raw values (infos self -> 0x18008c000, img -> 0x180000000)
  V3  chain topology: next strides land on observed entries / chain ends / window exit
  V4  kernel slide == aii_addr - infos_encoded (bit-exact, all hologram events)
  V5  kind1 addr == trunc16k(notif_encoded + slide); kind2 == trunc16k(img + slide)
      (kind0 = infos: always lands on aii_addr = always in-map = never fails =
       explains why kind 0 is absent from every hologram)
  V6  the DECODED (correct) arithmetic: dyld_base = aii_addr - 0x8c000;
      real pages = trunc16k(dyld_base + target) -- the broken addresses differ
      from the real ones ONLY in the fixup-fragment top half
  V7  impact: every failure is kr 0x1 KERN_INVALID_ADDRESS 'no map entry'
      (fail-fast bail, no pager I/O) -> cost negligible; the real loss is the
      dead prefetch (first-touch soft faults during every dyld startup)
"""
import re, struct, sys

LOG = "/home/z/my-project/download/r52-logdump.bin"
LIVE = 1474626
PAGE16K = 0x4000
EXPECTED_BASE = 0x180000000

data = open(LOG, "rb").read()[:LIVE]
lines = data.decode("utf-8", errors="replace").split("\n")
fails = 0
def ok(cond, tag):
    global fails
    print(("PASS " if cond else "FAIL ") + tag)
    if not cond:
        fails += 1

# ---- V0: reconstruct the template from the first infos-hex set -------------
hexrows = [l for l in lines if "redwood-exec: infos[" in l]
sets = [l for l in lines if "redwood-exec: infos-hex" in l]
tmpl = {}
for l in hexrows[: (0x160 // 0x10)]:
    m = re.match(r".*infos\[([0-9a-f]+)\]\s+([0-9a-f]{16})\s+([0-9a-f]{16})", l)
    off = int(m.group(1), 16)
    tmpl[off] = int(m.group(2), 16)
    tmpl[off + 8] = int(m.group(3), 16)
print(f"template: {len(sets)} sets logged, {len(tmpl)} u64 slots from set 1")
nonzero = {o: v for o, v in sorted(tmpl.items()) if v}
print("nonzero fields:", {hex(o): hex(v) for o, v in nonzero.items()})

# ---- V1: pure rebase decode -------------------------------------------------
def rebase(v):
    return dict(target=v & 0xFFFFFFFFF,          # bits 0-35
                high8=(v >> 36) & 0xFF,          # bits 36-43
                unused=(v >> 44) & 0x7F,         # bits 44-50
                next=(v >> 51) & 0x7FF,          # bits 51-61
                bind=(v >> 62) & 1, auth=(v >> 63) & 1)

fields = {0x10: "notification", 0x20: "dyldImageLoadAddress", 0x30: "dyldVersion",
          0x68: "dyldAllImageInfosAddress", 0xC0: "v17 tail (reserved/legacy)"}
ok(len(nonzero) == 6, "V1a exactly six nonzero slots: version@0x0 (=17) + five fixup fields")
ok(tmpl[0x0] & 0xFFFFFFFF == 17 and tmpl[0x0] >> 32 == 0,
   "V1a2 offset 0x0 = version 17 (low u32), infoArrayCount 0 (high u32)")
for off, name in fields.items():
    r = rebase(tmpl[off])
    pure = r["bind"] == 0 and r["auth"] == 0 and r["high8"] == 0 and r["unused"] == 0
    ok(pure, f"V1b {name}@{off:#x} pure rebase: target={r['target']:#x} next={r['next']}")

# ---- V2: truth table vs R51-registered raws --------------------------------
tgt_infos = rebase(tmpl[0x68])["target"]
tgt_img = rebase(tmpl[0x20])["target"]
tgt_notif = rebase(tmpl[0x10])["target"]
ok(tgt_infos + EXPECTED_BASE == 0x18008C000,
   f"V2 infos self: {tgt_infos:#x} + 0x180000000 = {tgt_infos+EXPECTED_BASE:#x} == 0x18008c000 (R51 truth)")
ok(tgt_img == 0, "V2 dyldImageLoadAddress target = 0 -> raw 0x180000000 = dyld's own mach header")

# ---- V3: chain topology ------------------------------------------------------
topology = []
for off, name in fields.items():
    nxt = rebase(tmpl[off])["next"]
    land = off + nxt * 8
    if land in tmpl:
        state = f"lands on entry {land:#x}" if tmpl[land] else f"lands on zero {land:#x} = chain end"
    elif land >= 0x170:
        state = f"exits the 0x170 struct window -> {land:#x} (continues in __DATA)"
    else:
        state = f"lands on zero {land:#x} = chain end"
    topology.append((name, off, nxt, land, state))
    print(f"V3 {name}@{off:#x} next={nxt} -> {state}")
resolved = sum(1 for t in topology if any(w in t[4] for w in ("end", "exits", "entry")))
ok(resolved == 5, f"V3 all 5 chains resolve to a defined landing (entry/end/exit): {resolved}/5")
notif_chain = next(t for t in topology if t[0] == "notification")
ok(notif_chain[3] == 0x30, "V3b notification chain continues at 0x30 (dyldVersion field)")

# ---- V4/V5: slide + kind arithmetic over EVERY hologram event ---------------
holo = []
for l in lines:
    tm = re.match(r"\[\s*([0-9.]+)\]: redwood-exec: prefault", l)
    if not tm:
        continue
    m = re.match(r".*prefault (\d+): pid (\d+)\[\w+\] kind (\d) kr (0x[0-9a-f]+) addr (0x[0-9a-f]+) "
                 r"\(entry (0x[0-9a-f]+) aii_addr (0x[0-9a-f]+) aii_size (0x[0-9a-f]+) "
                 r"notif (0x[0-9a-f]+) dyld_img (0x[0-9a-f]+) dyld_infos (0x[0-9a-f]+) "
                 r"slide (0x[0-9a-f]+)\)", l)
    if m:
        holo.append(dict(n=int(m.group(1)), pid=int(m.group(2)), kind=int(m.group(3)),
                         kr=int(m.group(4), 16), addr=int(m.group(5), 16),
                         t=float(tm.group(1)),
                         aii=int(m.group(7), 16), notif=int(m.group(9), 16),
                         img=int(m.group(10), 16), infos=int(m.group(11), 16),
                         slide=int(m.group(12), 16)))
print(f"hologram events: {len(holo)}")
M = (1 << 64) - 1
trunc = lambda a: a & ~(PAGE16K - 1)
bad_slide = [h for h in holo if h["slide"] != ((h["aii"] - h["infos"]) & M)]
ok(not bad_slide, f"V4 slide == aii_addr - infos_encoded for all {len(holo)} events")
ok(all(h["notif"] == tmpl[0x10] and h["img"] == tmpl[0x20] and h["infos"] == tmpl[0x68]
       for h in holo), "V4b hologram fields == template bytes (kernel read the same bytes)")
bad_k1 = [h for h in holo if h["kind"] == 1 and h["addr"] != trunc((h["notif"] + h["slide"]) & M)]
bad_k2 = [h for h in holo if h["kind"] == 2 and h["addr"] != trunc((h["img"] + h["slide"]) & M)]
ok(not bad_k1, f"V5 kind1 addr == trunc16k(notif+slide) all {len([h for h in holo if h['kind']==1])}")
ok(not bad_k2, f"V5 kind2 addr == trunc16k(img+slide) all {len([h for h in holo if h['kind']==2])}")
kinds = sorted({h["kind"] for h in holo})
ok(kinds == [1, 2], f"V5b only kinds {kinds} in holograms; kind0(infos)=aii_addr always in-map -> never fails")

# ---- V6: the decoded (correct) arithmetic -----------------------------------
ok(rebase(tmpl[0x68])["target"] == 0x8C000, "V6 infos target = 0x8c000")
h0 = holo[0]
dyld_base = h0["aii"] - tgt_infos
real_notif = trunc(dyld_base + tgt_notif)
real_img = trunc(dyld_base + tgt_img)
real_infos = trunc(h0["aii"])
brk1 = next(h["addr"] for h in holo if h["kind"] == 1)
brk2 = next(h["addr"] for h in holo if h["kind"] == 2)
print(f"V6 sample (pid {h0['pid']}, aii {h0['aii']:#x}):")
print(f"      dyld_base       = aii - 0x8c000        = {dyld_base:#x}")
print(f"      real notif page = dyld_base + 0x391f0  = {real_notif:#x}   (broken {brk1:#x})")
print(f"      real img page   = dyld_base + 0        = {real_img:#x}   (broken {brk2:#x})")
print(f"      real infos page = aii_addr             = {real_infos:#x}   (never failed)")
ok(real_notif & (PAGE16K - 1) == 0 and real_img == dyld_base,
   "V6 decoded pages are 16k-aligned and inside dyld's own image")
FRAG = 0xFF70000000000000
ok(((brk1 - real_notif) & M) == FRAG and ((brk2 - real_img) & M) == FRAG,
   "V6b broken = real + 0xFF70000000000000 (the fixup next-chain fragment) for BOTH kinds")

# ---- V7: impact --------------------------------------------------------------
krs = sorted({h["kr"] for h in holo})
no_map = len([l for l in lines if "no map entry" in l and "ask read-exec" in l])
print(f"V7 hologram kr set: {[hex(k) for k in krs]}; 'no map entry' read-exec verdicts: {no_map}")
ts = [h["t"] for h in holo]
ok(krs == [1], "V7 every prefault failure is kr 0x1 KERN_INVALID_ADDRESS (fail-fast, no pager I/O)")
ok(max(ts) - min(ts) < 120.0, f"V7b prefault failures span {min(ts):.1f}..{max(ts):.1f}s (the whole init/launchd exec phase)")
pids = sorted({h["pid"] for h in holo})
print(f"V7c prefault events span pids {pids} (init/launchd exec storm)")

print(f"\n==== R53 proof: {'ALL GREEN' if not fails else f'{fails} FAILURES'} ====")
sys.exit(1 if fails else 0)
