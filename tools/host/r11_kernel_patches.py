#!/usr/bin/env python3
"""R11 kernel-side patches: framebuffer software dimming (MiniIOMobileFramebuffer.cpp),
the /dev/redwood-touch type-6 dim record (redwood_input.c), and the conf/files
registration swap in redwood.patch (redwood goodix/buttons/adreno -> raphael_*)."""
import sys

BASE = "/home/z/my-project/work/hackios-xnu-kernel/xnu/"

def patch(path, pairs):
    p = BASE + path
    s = open(p).read()
    for old, new in pairs:
        n = s.count(old)
        if n == 0:
            print(f"MISS in {path}: {old[:70]!r}")
            sys.exit(1)
        if n > 1:
            print(f"AMBIGUOUS ({n}) in {path}: {old[:70]!r}")
            sys.exit(1)
        s = s.replace(old, new)
    open(p, "w").write(s)
    print(f"patched {path}")

# ---------------- MiniIOMobileFramebuffer.cpp: software dimming ----------------
fb = []

fb.append((
'''/* Boot-arg fbtrace=N: log the first N calls of every selector (default 3). */
static TUNABLE(uint32_t, gFBTrace, "fbtrace", 3);
''',
'''/* Boot-arg fbtrace=N: log the first N calls of every selector (default 3). */
static TUNABLE(uint32_t, gFBTrace, "fbtrace", 3);
/*
 * R11 (raphael): software dimming, boot-arg fbdim=P (a percent of full brightness, 10 to
 * 100; the default 100 leaves every pixel as it is). The panel's own backlight is the
 * AMOLED's, set over the display bus (DCS 0x51), which this kernel does not drive yet;
 * scaling the colours on their way to the panel is what is possible now, and on an AMOLED
 * less light is real power too. redwood_fb_set_dim() -- a record of type 6 written to
 * /dev/redwood-touch, x the percent -- changes it while the system runs.
 */
static TUNABLE(uint32_t, gFBDimPercent, "fbdim", 100);
static uint32_t gFBDim256;            /* 0 = not worked out yet; else 0..256 */
static inline uint32_t
fbDim(uint32_t c)
{
	uint32_t br = gFBDim256, r, g, b;

	if (br >= 256 || br == 0) {
		return c;
	}
	r = (((c >> 16) & 0xff) * br) >> 8;
	g = (((c >> 8) & 0xff) * br) >> 8;
	b = ((c & 0xff) * br) >> 8;
	return (c & 0xff000000u) | r << 16 | g << 8 | b;
}
void
redwood_fb_set_dim(uint32_t percent)
{
	if (percent < 10) {
		percent = 10;
	}
	if (percent > 100) {
		percent = 100;
	}
	gFBDim256 = percent * 256 / 100;
	IOLog("MiniIOMobileFramebuffer: the panel dimmed to %u%%\\n", (uint32_t)percent);
}
'''))

# the unscaled fast path: a dimmed copy when dimming is on
fb.append((
'''\t\tif (job->unscaled) {
\t\t\t/* not scaled at all */
\t\t\tmemcpy(to + px0, pixels + (size_t) (sy + y - py0) * stride + (size_t) sx * 4, (size_t) sw * 4);
\t\t\tcontinue;
\t\t}
''',
'''\t\tif (job->unscaled) {
\t\t\t/* not scaled at all */
\t\t\tif (gFBDim256 >= 256) {
\t\t\t\tmemcpy(to + px0, pixels + (size_t) (sy + y - py0) * stride + (size_t) sx * 4, (size_t) sw * 4);
\t\t\t} else {
\t\t\t\tconst uint32_t *src = (const uint32_t *) (pixels + (size_t) (sy + y - py0) * stride + (size_t) sx * 4);
\t\t\t\tfor (int32_t x = 0; x < sw; x++) {
\t\t\t\t\tto[px0 + x] = fbDim(src[x]);
\t\t\t\t}
\t\t\t}
\t\t\tcontinue;
\t\t}
'''))

# the filter and the plain paths: dim on the way out
fb.append((
'''\t\t\t\tout[i] = w == 0 || a == from[c + 1] ? a : blend(a, from[c + 1], w);
''',
'''\t\t\t\tout[i] = fbDim(w == 0 || a == from[c + 1] ? a : blend(a, from[c + 1], w));
'''))
fb.append((
'''\t\t\tfor (int32_t x = cx0; x < cx1; x++) {
\t\t\t\tto[x] = from[fColumn[x]];
\t\t\t}
''',
'''\t\t\tfor (int32_t x = cx0; x < cx1; x++) {
\t\t\t\tto[x] = fbDim(from[fColumn[x]]);
\t\t\t}
'''))

# work the percent out once, on the presenter thread, before the helpers see it
fb.append((
'''	bool filter = gFilter != 0;
	RowJob job;
''',
'''	bool filter = gFilter != 0;
	RowJob job;

	if (gFBDim256 == 0) {
		gFBDim256 = gFBDimPercent > 100 ? 256 : gFBDimPercent * 256 / 100;
	}
'''))

patch("files/iokit/Kernel/MiniIOMobileFramebuffer.cpp", fb)

# ---------------- redwood_input.c: the type-6 record sets the dim ----------------
inp = []
inp.append((
'''static int
touch_open(dev_t dev, int flags, int devtype, struct proc *p);
''',
'''extern void redwood_fb_set_dim(uint32_t percent);

static int
touch_open(dev_t dev, int flags, int devtype, struct proc *p);
'''))
# the write path: find where records are consumed. touch_post(t.type, t.finger, t.x, t.y, false)
inp.append((
'''\t\ttouch_post(t.type, t.finger, t.x, t.y, false);
''',
'''\t\tif (t.type == 6) {\t/* R11: not a touch: the panel's dimming, x a percent (10..100) */
\t\t\tredwood_fb_set_dim(t.x);
\t\t\tcontinue;
\t\t}
\t\ttouch_post(t.type, t.finger, t.x, t.y, false);
'''))
patch("files/bsd/dev/redwood_input.c", inp)

# ---------------- redwood.patch: build raphael's drivers instead of redwood's ----------------
pat = []
pat.append((
'''+bsd/dev/redwood_goodix.c\tstandard
+bsd/dev/redwood_buttons.c\tstandard
''',
'''+bsd/dev/raphael_touch.c\tstandard
+bsd/dev/raphael_buttons.c\tstandard
'''))
pat.append((
'''+bsd/dev/redwood_adreno.c\tstandard
''',
'''+bsd/dev/raphael_adreno.c\tstandard
'''))
patch("redwood.patch", pat)

print("all R11 kernel patches applied")
