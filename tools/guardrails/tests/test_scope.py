"""Guard against unrelated work and missing file types selecting expensive jobs."""

import unittest

from guardrails.scope import selected


class ScopeTests(unittest.TestCase):
    def test_docs_do_not_select_source(self) -> None:
        self.assertFalse(selected(["README.md"], ["a.rs"], ["*.rs"], ["*.rs"], []))

    def test_own_configuration_requires_present_type(self) -> None:
        self.assertTrue(selected(["check.yml"], ["a.rs"], ["*.rs"], ["*.rs"], ["check.yml"]))
        self.assertFalse(selected(["check.yml"], ["README.md"], ["*.rs"], ["*.rs"], ["check.yml"]))

    def test_deletion_selects_remaining_type(self) -> None:
        self.assertTrue(selected(["deleted.py"], ["other.py"], ["*.py"], ["*.py"], []))
        self.assertFalse(selected(["deleted.py"], [], ["*.py"], ["*.py"], []))

    def test_generated_paths_do_not_select(self) -> None:
        self.assertFalse(selected(["vendor/a.rs"], ["a.rs"], ["*.rs"], ["*.rs"], []))
