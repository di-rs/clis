"""Offline, original Git graphs: no remotes or caller-owned state."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import selection


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-q', '-b', 'master')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.base = self.commit('base')
        self.head = self.commit('head')
        self.git('checkout', '-q', '-b', 'target', self.base)
        self.target = self.commit('target')
        self.git('merge', '--no-ff', '-m', 'synthetic', self.head)
        self.synthetic = self.git('rev-parse', 'HEAD')
        self.event = {'pull_request': {'head': {'sha': self.head}, 'base': {'sha': self.target}}}

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, name):
        (self.repo / name).write_text(name)
        self.git('add', name)
        self.git('commit', '-qm', name)
        return self.git('rev-parse', 'HEAD')

    def test_pr_uses_exact_head_and_target_merge_base_not_synthetic_checkout(self):
        result = selection.select('pull_request', self.event, self.repo, self.synthetic)
        self.assertEqual(result, {'candidate_sha': self.head, 'target_sha': self.target, 'previous_sha': self.base})
        self.assertNotEqual(result['candidate_sha'], self.synthetic)

    def test_default_branch_push_uses_candidate_first_parent(self):
        event = {'after': self.head, 'ref': 'refs/heads/master', 'repository': {'default_branch': 'master'}}
        self.assertEqual(selection.select('push', event, self.repo, self.synthetic),
                         {'candidate_sha': self.head, 'target_sha': None, 'previous_sha': self.base})

    def test_root_commit_reports_missing_parent(self):
        event = {'after': self.base, 'ref': 'refs/heads/master', 'repository': {'default_branch': 'master'}}
        with self.assertRaisesRegex(ValueError, 'parent'):
            selection.select('push', event, self.repo, self.base)

    def test_non_default_push_rejected(self):
        event = {'after': self.head, 'ref': 'refs/heads/other', 'repository': {'default_branch': 'master'}}
        with self.assertRaises(ValueError):
            selection.select('push', event, self.repo, self.head)

    def test_explicit_manual_and_reusable_shas(self):
        for event in ('workflow_dispatch', 'workflow_call'):
            with self.subTest(event=event):
                result = selection.select(event, {}, self.repo, self.synthetic, self.head, self.target)
                self.assertEqual(result['previous_sha'], self.base)
                self.assertEqual(result['candidate_sha'], self.head)

    def test_malicious_or_symbolic_inputs_never_execute(self):
        for bad in ('HEAD', '--help', 'a' * 39, 'a' * 41, '$(touch PWNED)', self.head + '\nkey=value'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                selection.select('workflow_dispatch', {}, self.repo, self.synthetic, bad, self.target)
        self.assertFalse((self.repo / 'PWNED').exists())

    def test_pr_inputs_cannot_replace_event_identity(self):
        with self.assertRaises(ValueError):
            selection.select('pull_request', self.event, self.repo, self.synthetic, self.synthetic, self.target)

    def test_missing_sha_rejected(self):
        with self.assertRaises(ValueError):
            selection.select('workflow_dispatch', {}, self.repo, self.synthetic, '1' * 40, self.target)


if __name__ == '__main__':
    unittest.main()
