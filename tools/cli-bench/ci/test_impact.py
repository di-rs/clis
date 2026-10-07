"""Original commit graphs exercise exact previous/candidate package impact."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import impact


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-q', '-b', 'master')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.write('Cargo.toml', '[workspace]\nmembers=["biggie","coreutils/*","utils/*"]\n')
        for path, name, dependency in [('biggie', 'biggie', '../../utils/leaf'), ('coreutils/tailr', 'tailr', '../../utils/shared'), ('coreutils/mkdirr', 'mkdirr', ''), ('utils/shared', 'shared', '../leaf'), ('utils/leaf', 'leaf', '')]:
            text = '[package]\nname="' + name + '"\nversion="0.1.0"\n'
            if dependency and name != 'biggie':
                text += '[dependencies]\nhelper={path="' + dependency + '"}\n'
            self.write(path + '/Cargo.toml', text)
            self.write(path + '/src/lib.rs', '// initial')
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, stderr=subprocess.PIPE).strip()

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def selected(self):
        return impact.affected(self.repo, self.base, self.commit())

    def test_source_selects_only_own_cli(self):
        self.write('coreutils/mkdirr/src/lib.rs', '// change')
        self.assertEqual(self.selected(), ['mkdirr'])

    def test_transitive_source_selects_consumers(self):
        self.write('utils/leaf/src/lib.rs', '// changed dependency')
        self.assertEqual(self.selected(), ['tailr'])

    def test_docs_tests_harness_and_unrelated_cli_do_not_benchmark(self):
        for path in ['README.md', 'biggie/tests/cli.rs', 'biggie/benches/README.md', 'tools/cli-bench/src/lib.rs', '.github/workflows/cli-bench.yml', 'coreutils/catr/src/lib.rs']:
            self.write(path, '// unrelated')
        self.assertEqual(self.selected(), [])

    def test_shared_build_configuration_selects_all(self):
        for path in ['Cargo.lock', 'Cargo.toml', 'rust-toolchain.toml', '.cargo/config.toml']:
            with self.subTest(path=path):
                self.git('reset', '--hard', self.base)
                self.write(path, self.repo.joinpath(path).read_text() + '\n# change' if self.repo.joinpath(path).exists() else '# changed')
                self.assertEqual(self.selected(), ['biggie', 'tailr', 'mkdirr'])

    def test_manifest_build_script_and_suite_select_own_cli(self):
        for path in ['coreutils/tailr/Cargo.toml', 'coreutils/tailr/build.rs', 'coreutils/tailr/benches/cli-bench.toml']:
            with self.subTest(path=path):
                self.git('reset', '--hard', self.base)
                target = self.repo / path
                self.write(path, (target.read_text() if target.exists() else '') + '\n# change')
                self.assertEqual(self.selected(), ['tailr'])

    def test_deleted_dependency_and_renamed_source_use_both_revision_graphs(self):
        self.git('rm', '-r', 'utils/leaf')
        self.write('utils/shared/Cargo.toml', '[package]\nname="shared"\nversion="0.1.0"\n')
        self.git('mv', 'coreutils/mkdirr/src/lib.rs', 'coreutils/mkdirr/src/other.rs')
        self.assertEqual(self.selected(), ['tailr', 'mkdirr'])

    def test_comparison_ignores_checkout_and_changes_after_candidate(self):
        self.write('biggie/src/lib.rs', '// selected')
        candidate = self.commit()
        self.write('coreutils/mkdirr/src/lib.rs', '// later')
        self.commit()
        self.assertEqual(impact.affected(self.repo, self.base, candidate), ['biggie'])

    def test_manual_override_is_allowlisted_and_forbidden_for_pr(self):
        self.assertEqual(impact.override('tailr,mkdirr', 'workflow_dispatch'), ['tailr', 'mkdirr'])
        for value, event in [('tailr', 'pull_request'), ('$(touch PWNED)', 'workflow_dispatch'), ('tailr,tailr', 'workflow_dispatch'), ('unknown', 'workflow_dispatch')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                impact.override(value, event)

    def test_synchronize_impact_is_incremental_but_measurement_baseline_stays_fixed(self):
        self.write('biggie/src/lib.rs', '// earlier PR implementation')
        previous_head = self.commit()
        self.write('biggie/README.md', 'new docs only')
        candidate = self.commit()
        selected = {'candidate_sha': candidate, 'previous_sha': self.base, 'target_sha': self.base}
        event = {'action': 'synchronize', 'before': previous_head, 'after': candidate}
        self.assertEqual(impact.range_start('pull_request', event, selected), previous_head)
        self.assertEqual(impact.affected(self.repo, impact.range_start('pull_request', event, selected), candidate), [])
        self.assertEqual(selected['previous_sha'], self.base)
        self.assertEqual(impact.range_start('pull_request', {'action': 'opened'}, selected), self.base)
        self.assertEqual(impact.affected(self.repo, self.base, candidate), ['biggie'])

    def test_synchronize_requires_exact_available_previous_head_and_matching_after(self):
        selection = {'candidate_sha': self.base, 'previous_sha': self.base}
        for event in [{'action': 'synchronize'}, {'action': 'synchronize', 'before': '0' * 40, 'after': self.base},
                      {'action': 'synchronize', 'before': self.base, 'after': 'a' * 40}]:
            with self.subTest(event=event), self.assertRaises(ValueError):
                impact.range_start('pull_request', event, selection)
        with self.assertRaises(ValueError):
            impact.affected(self.repo, 'f' * 40, self.base)

    def test_workspace_inherited_and_target_build_dependencies_propagate(self):
        self.write('Cargo.toml', '[workspace]\nmembers=["biggie","coreutils/*","utils/*"]\n[workspace.dependencies]\nshared={path="utils/shared"}\n')
        self.write('coreutils/mkdirr/Cargo.toml', '[package]\nname="mkdirr"\nversion="0.1.0"\n[target."cfg(unix)".build-dependencies]\nshared={workspace=true}\n')
        self.base = self.commit()
        self.write('utils/leaf/src/lib.rs', '// inherited build dependency')
        self.assertEqual(self.selected(), ['tailr', 'mkdirr'])

    def test_cargo_docs_do_not_count_as_build_settings(self):
        self.write('.cargo/README.md', 'only documentation')
        self.assertEqual(self.selected(), [])
