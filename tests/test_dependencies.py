"""验证缺失/损坏工具和不完整构建不会被误报为依赖通过。"""
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dependencies


class DependencyTests(unittest.TestCase):
    def test_missing_binaries(self):
        with patch.object(dependencies.shutil, 'which', return_value=None):
            report = dependencies.doctor(smoke=True)
        self.assertFalse(report['local_ready'])
        self.assertFalse(report['binaries']['ffmpeg']['available'])
        self.assertEqual(report['smoke']['status'], 'skipped')
        self.assertTrue(all(row['status'] == 'unverified' for row in report['external'].values()))

    def test_broken_binary(self):
        with patch.object(dependencies.shutil, 'which', return_value='broken'), \
             patch.object(dependencies, 'execute', side_effect=OSError('cannot execute')):
            report = dependencies.doctor()
        self.assertFalse(report['local_ready'])
        self.assertFalse(report['binaries']['ffprobe']['available'])

    def test_missing_encoders(self):
        def fake_execute(command):
            return 'test version' if '-version' in command else ''
        with patch.object(dependencies.shutil, 'which', return_value='mock'), \
             patch.object(dependencies, 'execute', side_effect=fake_execute):
            report = dependencies.doctor()
        self.assertFalse(report['local_ready'])
        self.assertIn('libx264', report['capabilities']['encoders']['missing'])

    def test_capability_parser(self):
        self.assertEqual(dependencies.names(' V....D libx264 description\n D mov,mp4 description\n ... scale description'),
                         {'libx264', 'mov', 'mp4', 'scale'})

    def test_python310_syntax(self):
        for path in (dependencies.ROOT / 'scripts').glob('*.py'):
            ast.parse(path.read_text(encoding='utf8'), feature_version=(3, 10))


if __name__ == '__main__':
    unittest.main()
