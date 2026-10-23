#!/usr/bin/env python3
"""R11 GPU corrections for raphael_adreno.c, from the msm catalog and the firmware files
themselves:

1. The GMU firmware of chipid 0x06040001 (a640.1) is "a640_gmu.bin" (a6xx_catalog.c: the
   a640's entry), not the a630's raw file: the catalog names them separately, and the
   a630_gmu.bin in linux-firmware is a RAW image for the a630's "legacy" load path
   (a6xx_gmu.c: adreno_is_a630_family -> gmu->legacy -> gmu_write_bulk into the ITCM
   window), while the a640 is GEN2 (adreno_is_a640_family) and its file is the
   block-walking format this driver parses (decoded and confirmed: ITCM 0x0..0x4000,
   DTCM 0x40000..0x44000, and one block at 0x57000 -- inside the "dcache" pool).
2. That file asks for no memory of its own (no "NEEDS" blocks): its non-TCM data goes
   where Linux allocates the GEN2 pools for it (a6xx_gmu.c: icache 0x4000 and dcache
   0x44000, SZ_256K - SZ_16K each) -- make those pools when the file writes there.
3. The zap shader of sm8150 is a single-file "a640_zap.mbn" (linux-firmware
   qcom/sm8150/a640_zap.mbn; the phone's own dts names its own, same form): an ELF32
   whose hash segment (p_flags type 2) and one PT_LOAD segment (relocatable, p_flags bit
   27) are both inside the one file. The .mdt-plus-.b02 of the redwood phone becomes:
   the header handed to TrustZone = the ELF header + the hash segment copied out; the
   segment written to the reserved memory = the PT_LOAD segment, from the same file.
"""
import sys

SRC = "/home/z/my-project/work/hackios-xnu-kernel/xnu/files/bsd/dev/raphael_adreno.c"
s = open(SRC).read()

def sub1(old, new, must=True):
    global s
    n = s.count(old)
    if n == 0 and must:
        print(f"MISS: {old[:80]!r}")
        sys.exit(1)
    s = s.replace(old, new)
    return n

# 1. the firmware file's name
sub1('a630_gmu.bin', 'a640_gmu.bin')

# the files[] presence check: no .b02 any more, the .mbn in its place
sub1('static const char *const files[] = { "a640_gmu.bin", "a630_sqe.fw", "a640_zap.mdt", "a640_zap.b02" };',
     'static const char *const files[] = { "a640_gmu.bin", "a630_sqe.fw", "a640_zap.mbn" };')

# 2. the GEN2 pools: make them where the firmware writes without asking
sub1('''		if (gmu_block_count == sizeof(gmu_blocks) / sizeof(gmu_blocks[0]) || (block.address & 4095) ||
		    block.value > 0x100000 || !block_new(&gmu_space, &gmu_blocks[gmu_block_count++], block.address, block.value,
		    PTE_GMU)) {
			printf("adreno: %u: cannot make %#x bytes at %#x for the firmware\\n", step, block.value, block.address);
			return false;
		}
	}
''',
'''		if (gmu_block_count == sizeof(gmu_blocks) / sizeof(gmu_blocks[0]) || (block.address & 4095) ||
		    block.value > 0x100000 || !block_new(&gmu_space, &gmu_blocks[gmu_block_count++], block.address, block.value,
		    PTE_GMU)) {
			printf("adreno: %u: cannot make %#x bytes at %#x for the firmware\\n", step, block.value, block.address);
			return false;
		}
	}
	/*
	 * The a640's firmware (GEN2) asks for nothing: the block walk above made none of the
	 * memory it writes. Where its data lands is where Linux allocates the two caches for
	 * the a640 family (a6xx_gmu.c: "icache" at 0x4000 and "dcache" at 0x44000, 256K less
	 * 16K each); make those, once, where the file writes.
	 */
	{
		static const struct {
			uint32_t address, size;
			const char *name;
		} pools[] = {
			{ 0x04000, 0x3f000, "icache" },
			{ 0x44000, 0x3f000, "dcache" },
		};

		for (uint64_t at = 0; at + sizeof(struct gmu_firmware_block) <= gmu_firmware_size;) {
			struct gmu_firmware_block block;
			bool covered = false;

			memcpy(&block, gmu_firmware + at, sizeof(block));
			at += sizeof(block) + block.size;
			if (block.size == 0 || in_tcm(block.address)) {
				continue;
			}
			for (unsigned i = 0; i < gmu_block_count; i++) {
				if (block.address >= gmu_blocks[i].address &&
				    block.address + block.size <= gmu_blocks[i].address + gmu_blocks[i].size) {
					covered = true;
					break;
				}
			}
			if (covered) {
				continue;
			}
			for (unsigned p = 0; p < sizeof(pools) / sizeof(pools[0]); p++) {
				if (block.address >= pools[p].address &&
				    block.address + block.size <= pools[p].address + pools[p].size &&
				    gmu_block_count < sizeof(gmu_blocks) / sizeof(gmu_blocks[0]) &&
				    block_new(&gmu_space, &gmu_blocks[gmu_block_count++], pools[p].address, pools[p].size, PTE_GMU)) {
					printf("adreno: %u: %#x bytes at %#x: the %s the a640 family's firmware fills (where Linux puts it)\\n",
					    step, pools[p].size, pools[p].address, pools[p].name);
					covered = true;
					break;
				}
			}
			if (!covered) {
				printf("adreno: %u: the firmware has %#x bytes at %#x, where neither it nor this driver asked for memory\\n",
				    step, block.size, block.address);
				return false;
			}
		}
	}
''')

# 3a. the zap header: from a single-file .mbn, as qcom_mdt_read_metadata does
sub1('''static bool
zap_header(uint64_t *phys)
{
	const uint8_t *header;
	vm_offset_t page = 0;
	uint64_t size = 0;
	uint32_t segments, hashes;

	header = image_file("fw", "a640_zap.mdt", &size);
	if (header == NULL) {
		return false;
	}
	/* a 32-bit ELF: the count of segments at 44, the table of them at 52, 32 bytes each, the file size of one at +16 */
	segments = size >= 52 + 2 * 32 ? header[44] | header[45] << 8 : 0;
	if (memcmp(header, "\\177ELF\\1", 5) != 0 || segments < 2 || size > PAGE_SIZE) {
		printf("adreno: %u: that is not such a header (%02x %02x %02x %02x, %u segments)\\n", step, header[0], header[1],
		    header[2], header[3], segments);
		return false;
	}
	memcpy(&hashes, header + 52 + 32 + 16, sizeof(hashes));
	printf("adreno: %u: an ELF header of %u segments, %u bytes with its table, then %llu bytes; by the table, %u of hashes\\n",
	    step, segments, 52 + 32 * segments, size - (52 + 32 * segments), hashes);
	if (52 + 32 * segments + (uint64_t)hashes != size) {
		printf("adreno: %u: the file is not the header followed by the hashes alone: not what TrustZone takes\\n", step);
		return false;
	}
	if (kmem_alloc(kernel_map, &page, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("adreno: %u: no memory\\n", step);
		return false;
	}
	memcpy((void *)page, header, size);
	to_memory(page, PAGE_SIZE);             /* TrustZone reads memory, not this processor's cache */
	*phys = kvtophys(page);
	return true;
}''',
'''static bool
zap_header(uint64_t *phys)
{
	/*
	 * A single-file "mbn": an ELF32 whose hash segment (a program header whose p_flags'
	 * low three bits are 2) and whose loadable segment are both inside the one file, at
	 * their p_offsets. What TrustZone takes at the first call is the ELF header and the
	 * hash segment, put together with nothing between them (mdt_loader.c,
	 * qcom_mdt_read_metadata). The redwood phone's zap was an .mdt whose whole file was
	 * just that pair; this phone's carries the program too.
	 */
	const uint8_t *header;
	vm_offset_t page = 0;
	uint64_t size = 0;
	uint32_t segments, phoff, header_bytes = 0, hash_at = 0, hash_bytes = 0;

	header = image_file("fw", "a640_zap.mbn", &size);
	if (header == NULL) {
		return false;
	}
	/* a 32-bit ELF: the count of segments at 44, the table of them at 28, 32 bytes each, the file size of one at +16 */
	segments = size >= 52 + 2 * 32 ? header[44] | header[45] << 8 : 0;
	memcpy(&phoff, header + 28, sizeof(phoff));
	if (memcmp(header, "\\177ELF\\1", 5) != 0 || segments < 2 || segments > 16 ||
	    phoff != 52 || size < phoff + 32ull * segments) {
		printf("adreno: %u: that is not such a header (%02x %02x %02x %02x, %u segments)\\n", step, header[0], header[1],
		    header[2], header[3], segments);
		return false;
	}
	memcpy(&header_bytes, header + phoff + 16, sizeof(header_bytes));       /* phdr[0].p_filesz: the header pair's size */
	for (uint32_t i = 1; i < segments; i++) {
		uint32_t at = phoff + 32 * i, flags, off, len;

		memcpy(&flags, header + at + 24, sizeof(flags));
		memcpy(&off, header + at + 4, sizeof(off));
		memcpy(&len, header + at + 16, sizeof(len));
		if ((flags & 7u) == 2u) {          /* the hash segment, named by its type */
			hash_at = off;
			hash_bytes = len;
			break;
		}
	}
	printf("adreno: %u: an ELF of %u segments: the header pair %#x bytes, the hashes %#x at %#x, the whole file %llu bytes\\n",
	    step, segments, header_bytes, hash_bytes, hash_at, size);
	if (header_bytes == 0 || header_bytes > PAGE_SIZE || hash_bytes == 0 ||
	    (uint64_t)hash_at + hash_bytes > size || header_bytes + (uint64_t)hash_bytes > PAGE_SIZE) {
		printf("adreno: %u: that file's header and hashes do not make what TrustZone takes\\n", step);
		return false;
	}
	if (kmem_alloc(kernel_map, &page, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("adreno: %u: no memory\\n", step);
		return false;
	}
	memcpy((void *)page, header, header_bytes);
	memcpy((void *)(page + header_bytes), header + hash_at, hash_bytes);
	to_memory(page, PAGE_SIZE);             /* TrustZone reads memory, not this processor's cache */
	*phys = kvtophys(page);
	return true;
}''')

# 3b. the zap segment: from the .mbn's one PT_LOAD program header
sub1('''	segment = image_file("fw", "a640_zap.b02", &size);
	if (segment == NULL || size == 0 || size > 0x1000) {
		return false;
	}
''',
'''	{
		/* the one PT_LOAD segment, inside the same .mbn as the header (p_offset/p_filesz) */
		const uint8_t *file;
		uint64_t whole = 0;
		uint32_t segments, phoff, i;

		segment = NULL;
		size = 0;
		file = image_file("fw", "a640_zap.mbn", &whole);
		if (file != NULL && whole >= 52 + 2 * 32) {
			memcpy(&segments, file + 44, 2);
			memcpy(&phoff, file + 28, 4);
			for (i = 0; i < segments && phoff + 32ull * (i + 1) <= whole; i++) {
				uint32_t at = phoff + 32 * i, type, off, len;

				memcpy(&type, file + at, sizeof(type));
				memcpy(&off, file + at + 4, sizeof(off));
				memcpy(&len, file + at + 16, sizeof(len));
				if (type == 1 && len > 0 && len <= 0x1000 && (uint64_t)off + len <= whole) {      /* PT_LOAD */
					segment = file + off;
					size = len;
					break;
				}
			}
		}
	}
	if (segment == NULL || size == 0 || size > 0x1000) {
		return false;
	}
''')

# the announcements that name the .mdt
sub1('announce("find the zap shader\'s header, fw/a640_zap.mdt, in the small image that came with the kernel");',
     'announce("find the zap shader, fw/a640_zap.mbn, in the small image that came with the kernel");')
sub1(' * the ELF header and the block of hashes and certificates, which is all of the file a640_zap.mdt',
     ' * the ELF header and the block of hashes and certificates, taken out of the file a640_zap.mbn')

# header doc: the firmware note
sub1(' * The firmware is the phone\'s own: the GMU\'s and the SQE\'s from Linux\'s linux-firmware\n * (qcom/a640_gmu.bin, qcom/a630_sqe.fw -- the a630 family\'s files, which the a640 shares),\n * the zap shader from the phone\'s Android (a640_zap.mdt + a640_zap.b02), all in fw/ of the\n * small image.',
     ' * The firmware is from Linux\'s linux-firmware: qcom/a640_gmu.bin (the file the msm catalog\n * names for chipid 0x06040001) and qcom/a630_sqe.fw, and qcom/sm8150/a640_zap.mbn -- the\n * generic sm8150 zap shader, which the phone\'s own tree may replace with its own -- all in\n * fw/ of the small image.')
sub1(' *  12  asks    TrustZone to check the signature of the GPU\'s "zap shader", the signed image\n *              without which the 3D core stays locked in secure mode: its header goes from the\n *              small image (fw/a640_zap.mdt) into memory of ours that TrustZone is pointed at\n',
     ' *  12  asks    TrustZone to check the signature of the GPU\'s "zap shader", the signed image\n *              without which the 3D core stays locked in secure mode: its header and hashes go\n *              from the small image into memory of ours that TrustZone is pointed at\n')

open(SRC, "w").write(s)
print("raphael_adreno.c: a640 corrections applied")
print("a630_gmu refs left:", s.count("a630_gmu"))
print("zap.b02 refs left:", s.count("zap.b02"))
print("zap.mdt refs left:", s.count("zap.mdt"))
