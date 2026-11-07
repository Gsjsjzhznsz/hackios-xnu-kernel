#!/usr/bin/env python3
"""Exercise the var_store staging block without running the image build."""
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest

USERLAND = Path(__file__).resolve().parent.parent / 'userland'


class PackagingTests(unittest.TestCase):
    def stage(self, include_binary):
        build = (USERLAND / 'build.sh').read_text(encoding='utf-8')
        start = build.index('if [ -f obj/var_store ]; then\n')
        end = build.index('\nfi', start) + len('\nfi')
        block = build[start:end]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'obj').mkdir()
            (root / 'root/bin').mkdir(parents=True)
            (root / 'root/LaunchDaemons').mkdir()
            (root / 'com.hakios.varstore.plist').write_bytes(
                (USERLAND / 'com.hakios.varstore.plist').read_bytes())
            if include_binary:
                (root / 'obj/var_store').write_bytes(b'test binary')
            subprocess.run(['bash', '-e', '-c', block], cwd=root, check=True)
            executable = root / 'root/bin/var_store'
            job = root / 'root/LaunchDaemons/com.hakios.varstore.plist'
            self.assertEqual(executable.exists(), include_binary)
            self.assertEqual(job.exists(), include_binary)
            if include_binary:
                self.assertEqual(executable.read_bytes(), b'test binary')
                self.assertEqual(plistlib.loads(job.read_bytes())['ProgramArguments'][0],
                                 '/redwood/bin/var_store')

    def test_missing_program_omits_job(self):
        self.stage(False)

    def test_present_program_installs_both(self):
        self.stage(True)


if __name__ == '__main__':
    unittest.main()
