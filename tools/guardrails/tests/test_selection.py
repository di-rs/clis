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

    def test_packaging_inputs_select_integration_consumers(self) -> None:
        for path in [
            "packages/boxie/tests/test_packaging.py",
            "packages/boxie/infra/compose.yml",
            "packages/boxie/infra/compose.production.yml",
            "packages/boxie/infra/postgres/compose.yml",
            "packages/boxie/infra/Boxie.Dockerfile.dockerignore",
            "tools/cli-playground/infra/compose.production.yml",
            "tools/cli-playground/infra/docker/CliPlayground.Dockerfile.dockerignore",
        ]:
            with self.subTest(path=path):
                self.assertTrue(selection([path], [path, "ui.ts"])["frontend"])

    def test_removed_non_markdown_targets_select_link_checks(self) -> None:
        old = "tools/cli-playground/src/cli_playground/routes.py"
        new = "tools/cli-playground/src/cli_playground/http.py"
        for paths, present in [([old], ["README.md"]), ([old, new], ["README.md", new])]:
            with self.subTest(paths=paths):
                self.assertTrue(selection(paths, present)["markdown"])

    def test_modified_non_markdown_target_does_not_select_link_checks(self) -> None:
        path = "tools/cli-playground/src/cli_playground/routes.py"
        self.assertFalse(selection([path], ["README.md", path])["markdown"])

    def test_infrastructure_inputs_select_python_consumers(self) -> None:
        for path in [
            "packages/boxie/infra/compose.operations.yml",
            "packages/boxie/infra/observability/versions.json",
            "tools/cli-playground/infra/scripts/deploy.sh",
            "tools/cli-playground/infra/proxy/routes.yml",
            "infra/environments/single-host/release.sh",
            "infra/host/bootstrap.sh",
            "infra/host/storage-guard.sh",
            ".github/workflows/boxie-release.yml",
        ]:
            with self.subTest(path=path):
                self.assertTrue(selection([path], [path, "app.py"])["python"])

    def test_infrastructure_docs_only_skip_python(self) -> None:
        path = "packages/boxie/infra/README.md"
        self.assertFalse(selection([path], [path, "app.py"])["python"])
