#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "pptx_sidecars.py"
SPEC = importlib.util.spec_from_file_location("pptx_sidecars", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class DefRPrCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = MODULE.ET.fromstring(
            """
            <a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <a:pPr>
                <a:defRPr sz="3400" b="1">
                  <a:solidFill><a:srgbClr val="123456"/></a:solidFill>
                  <a:latin typeface="Helvetica Neue"/>
                </a:defRPr>
              </a:pPr>
              <a:r><a:t>default styled text</a:t></a:r>
              <a:r>
                <a:rPr sz="1800" b="0"/>
                <a:t>inline size override</a:t>
              </a:r>
            </a:p>
            """
        )

    def test_paragraph_default_run_properties_are_used(self):
        style = MODULE.resolve_run_style(self.root, self.root.findall("a:r", MODULE.NS)[0])
        self.assertEqual(style["size"], 3400)
        self.assertTrue(style["bold"])
        self.assertEqual(style["color"], "#123456")
        self.assertEqual(style["typeface"], "Helvetica Neue")

    def test_inline_properties_override_defaults_property_by_property(self):
        style = MODULE.resolve_run_style(self.root, self.root.findall("a:r", MODULE.NS)[1])
        self.assertEqual(style["size"], 1800)
        self.assertFalse(style["bold"])
        self.assertEqual(style["color"], "#123456")
        self.assertEqual(style["typeface"], "Helvetica Neue")


if __name__ == "__main__":
    unittest.main()
