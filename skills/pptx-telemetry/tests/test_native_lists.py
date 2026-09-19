import importlib.util
import pathlib
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('check_native_lists', pathlib.Path(__file__).parents[1] / 'scripts/check_native_lists.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def shape(size='1800', gaps=(1600, 1600), native=True, shrink=False):
    p = ''.join(f'''<a:p><a:pPr marL="228600" indent="-114300">
        <a:spcAft><a:spcPts val="{gaps[i] if i < 2 else 0}"/></a:spcAft>
        {'<a:buChar char="•"/>' if native else ''}<a:defRPr sz="{size}"/>
        </a:pPr><a:r><a:t>内容 {i}</a:t></a:r></a:p>''' for i in range(3))
    return ET.fromstring(f'''<p:sp xmlns:p="{module.NS['p']}" xmlns:a="{module.NS['a']}">
       <p:txBody><a:bodyPr>{'<a:normAutofit fontScale="80000"/>' if shrink else '<a:noAutofit/>'}</a:bodyPr>{p}</p:txBody></p:sp>''')


class NativeListChecks(unittest.TestCase):
    def test_correct_list(self):
        self.assertEqual(module.check_list(shape()), [])

    def test_original_slide11_failure_modes(self):
        issues = module.check_list(shape(size='1350', gaps=(0, 0), native=False))
        self.assertIn('FONT_BELOW_POINT_FLOOR', issues)
        self.assertIn('NATIVE_BULLET_MISSING', issues)
        self.assertIn('LIST_GAPS_NOT_EXPLICIT_AND_EQUAL', issues)

    def test_effective_size_includes_shrink_factor(self):
        self.assertIn('FONT_BELOW_POINT_FLOOR', module.check_list(shape(shrink=True)))

    def test_unequal_gaps(self):
        self.assertIn('LIST_GAPS_NOT_EXPLICIT_AND_EQUAL', module.check_list(shape(gaps=(800, 1600))))

    def test_small_inline_run_is_not_hidden_by_large_default(self):
        s = shape()
        run = s.find('.//a:r', module.NS)
        ET.SubElement(run, '{' + module.NS['a'] + '}rPr', {'sz': '1000'})
        self.assertIn('FONT_BELOW_POINT_FLOOR', module.check_list(s))
