/*
 * raphael: paste into xnuloader/loader.c next to platform_redwood and add &platform_raphael to platforms[].
 * Every value is explained in docs/PORTING.md ("The loader"). Values below are not TODO guesses:
 * GIC, cores, MPIDR numbering and the small cluster were read from the phone's real device tree
 * (vendor_boot DTB, 499823 bytes) with the same rules common/new-device.sh uses.
 */
static const struct platform platform_raphael = {
	/* io_base must be non-zero: XNU treats a zero SoC base as "no SoC" and skips clock setup. */
	.name = "raphael", .io_base = 0x00400000, .io_size = 0x3FC00000,
	.gic_dist = 0x17A00000, .gic_redist = 0x17A60000, .uart = 0, .log_other_cpu = 1,
	/*
	 * SM8150, 8 cores: 0-3 Kryo 485 Silver (Cortex-A55, capacity 1024), 4-6 Gold + 7 Prime
	 * (Cortex-A76, capacity 1740) - the same 4+4 shape as redwood, so the small-core mask
	 * and the core numbering carry over unchanged.
	 */
	.cores = 8, .core_shift = 8,
	.small_cores = 0x0f,
	/*
	 * cepheusPkg memory map: "Display Reserved" at 0x9C000000, 0x02400000 - 1080x2340x32
	 * with room for double buffering. The loader tells platforms apart by this base
	 * (redwood answers 0xE1000000, so the two do not collide).
	 */
	.fb_base = 0x9C000000,
};
