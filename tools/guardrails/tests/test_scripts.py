"""Script extraction preserves case-insensitive script blocks and line offsets."""

import unittest

from guardrails.scripts import script


class ScriptTests(unittest.TestCase):
    def test_uppercase(self) -> None:
        text = "<SCRIPT>\nconst x = 1;\n</SCRIPT>\n<p>text</p>"
        extracted = script("app.svelte", text)
        self.assertEqual(extracted.splitlines()[1], "const x = 1;")
        self.assertNotIn("<p>", extracted)
