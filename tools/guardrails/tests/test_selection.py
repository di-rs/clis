"""Regression coverage for complete CI consumer selection."""

import unittest

from guardrails.job_selection import selection


class SelectionTests(unittest.TestCase):
    def test_docs_only_skip_language_jobs(self) -> None:
        selected = selection(["README.md"], ["README.md", "lib.rs", "app.py", "ui.ts"])
        self.assertTrue(selected["markdown"])
        self.assertFalse(any(selected[k] for k in ["rust", "python", "frontend"]))

    def test_own_config_only_selects_present_types(self) -> None:
        selected = selection(["tools/guardrails/job_selection.py"], ["README.md", "app.py"])
        self.assertTrue(selected["python"])
        self.assertFalse(selected["rust"])
        self.assertFalse(selected["frontend"])

    def test_backend_tests_and_dependencies_select_integration_consumers(self) -> None:
        present = ["ui.ts", "app.py", "src/lib.rs", "uv.lock"]
        for path in [
            "tools/cli-playground/tests/test_api.py",
            "tools/cli-playground/pyproject.toml",
            "uv.lock",
        ]:
            self.assertTrue(selection([path], present)["frontend"])
        self.assertTrue(selection(["deny.toml"], present)["rust"])
