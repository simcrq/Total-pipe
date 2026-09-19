import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'design_land.py'
spec = importlib.util.spec_from_file_location('design_land', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DesignLandDefaults(unittest.TestCase):
    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_new_contract_and_explicit_legacy_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'contract.json'
            self.run_cli('contract', '--out', path)
            contract = json.loads(path.read_text())
            self.assertEqual(contract['minimum_font_pt'], 18)
            self.assertIsNone(contract['density_target_shapes_per_page'])
            self.run_cli('contract', '--out', path, '--min-font', '10.5', '--density', '22', '44')
            self.run_cli('contract', '--out', path)
            legacy = module.load_contract(path)
            self.assertEqual(legacy['minimum_font_pt'], 10.5)
            self.assertEqual(legacy['density_target_shapes_per_page'], [22, 44])

    def test_defaults_are_not_shared_mutable_state(self):
        first = module.load_contract(None)
        first['font_ladder_pt']['body'][0] = 1
        self.assertEqual(module.load_contract(None)['font_ladder_pt']['body'][0], 18)

    def test_sparse_page_has_no_default_density_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '01.slide').write_text('<Box style={{position: "absolute", left: 40, top: 40, width: 800, height: 400}}><Text style={{fontSize: 24}}>核心结论</Text></Box>')
            output = self.run_cli('check', '--slides', path)
            self.assertNotIn('LOW_DENSITY', output)
            self.assertNotIn('HIGH_DENSITY', output)
            contract = path / 'contract.json'
            self.run_cli('contract', '--out', contract, '--density', '22', '44')
            explicit = self.run_cli('check', '--slides', path, '--contract', contract)
            self.assertIn('LOW_DENSITY', explicit)
            self.run_cli('brief', '--skeleton', path)


if __name__ == '__main__':
    unittest.main()
