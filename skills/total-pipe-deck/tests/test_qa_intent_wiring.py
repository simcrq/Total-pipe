"""Driver wiring tests: mock Node execution, inspect actual serialized inputs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[2]


class IntentWiring(unittest.TestCase):
    def test_both_drivers_preserve_page_intent_and_viewing_mode(self):
        for driver in ('artifact-telemetry', 'pptx-telemetry'):
            script = SKILLS / driver / 'scripts' / 'run_qa.py'
            with patch.object(sys, 'path', [str(script.parent), *sys.path]):
                spec = importlib.util.spec_from_file_location(driver, script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            for first_index in (0, 1):
                with self.subTest(driver=driver, first_index=first_index), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    sidecars = root / 'sidecars'
                    sidecars.mkdir()
                    tag = 'slide-01-scope' if driver == 'artifact-telemetry' else 'slide-01'
                    (sidecars / (tag + '.json')).write_text('{}')
                    intent = {'objective': 'scope', 'required_on_screen': ['2 nm MoS₂']}
                    plan = root / 'plan.json'
                    plan.write_text(json.dumps({'slides': [{'index': first_index,
                        'design_ir': {'presentation_intent': intent}}]}))
                    commands = {}
                    font_status = 'pass'

                    def fake_run(node, command, out):
                        operation = command[1]
                        source = json.loads(Path(command[3]).read_text())
                        commands[operation] = source
                        if operation == 'assemble-render-telemetry':
                            result = {'status': 'pass', 'canonical_telemetry': {'slide': {}, 'elements': []}}
                        elif operation == 'visual-quality':
                            result = {'status': 'pass', 'violations': [], 'checks': {
                                'projector_typography': {'status': font_status},
                                'presentation_intent': {'status': 'pass'}}}
                        else:
                            result = {'status': 'valid', 'pipeline_status': 'render_qa_complete', 'invalid_slides': []}
                        Path(out).write_text(json.dumps(result))
                        return 0

                    argv = [str(script), '--sidecars', str(sidecars), '--work', str(root / 'qa'),
                            '--plan', str(plan), '--viewing-mode', 'screen']
                    with patch.object(sys, 'argv', argv), patch.object(module, 'run', fake_run), contextlib.redirect_stdout(io.StringIO()):
                        collision = getattr(module, 'pairwise_collision', None)
                        with contextlib.ExitStack() as stack:
                            if collision:
                                stack.enter_context(patch.object(collision, 'check_sidecar', return_value={
                                    'status': 'pass', 'violations': [], 'checked_pairs': 0, 'true_collisions': 0}))
                            self.assertEqual(module.main(), 0)
                            font_status = 'not_evaluable'
                            self.assertEqual(module.main(), 1)
                            self.assertEqual(json.loads((root / 'qa' / 'summary.json').read_text())['release_status'], 'blocked')
                    visual = commands['visual-quality']
                    self.assertEqual(visual['presentation_intent'], intent)
                    self.assertEqual(visual['context'], {'viewing_mode': 'screen', 'enforce_typography': True})
                    rendered = commands['validate-rendered-deck']['slides'][0]
                    self.assertEqual(rendered['presentation_intent'], intent)
                    self.assertTrue(rendered['enforce_typography'])
                    self.assertIn('telemetry', rendered)


if __name__ == '__main__':
    unittest.main()
