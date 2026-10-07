"""Compact output projections preserve Rust observations without remote evidence."""
import json
from pathlib import Path
import tempfile
import unittest

import summary


def record():
    return {'publication': {'result': {'outcome': 'complete'}, 'analysis': {
        'cases': [{'id': 'tiny', 'roles': {'candidate': {'elapsed': {'mean': 1.25}, 'rss': {'mean': 42}, 'executable_bytes': 1234}},
                   'comparisons': [{'baseline': 'previous', 'candidate': 'candidate', 'ratio': 0.9,
                                    'elapsed_change_percent': -10.0, 'direction': 'faster'}]}],
        'issues': []}}}


class SummaryTests(unittest.TestCase):
    def test_projects_existing_observations_without_recomputation(self):
        value = summary.project('tailr', record(), None)
        self.assertEqual(value['outcome'], 'complete')
        self.assertEqual(value['cases'][0]['roles'], [{'role': 'candidate', 'elapsed_mean': 1.25, 'rss_mean': 42, 'executable_bytes': 1234}])
        self.assertEqual(value['cases'][0]['comparisons'][0]['ratio'], 0.9)
        self.assertEqual(value['cases'][0]['comparisons'][0]['elapsed_change_percent'], -10.0)

    def test_missing_failed_and_omitted_cases_are_explicit(self):
        value = record()
        value['publication']['analysis']['cases'] *= 128
        result = summary.project('tailr', value, None)
        self.assertEqual(len(result['cases']), 12)
        self.assertEqual(result['omitted_cases'], 116)
        self.assertEqual(summary.project('tailr', None, None)['outcome'], 'unavailable')
        failure = {'stage': 'build', 'message': 'compiler failed', 'exit_code': 1}
        self.assertEqual(summary.project('tailr', None, failure)['failure'], 'build: compiler failed')

    def test_projection_rejects_nonfinite_values_and_keeps_failure(self):
        value = record()
        value['publication']['analysis']['cases'][0]['roles']['candidate']['elapsed']['mean'] = float('nan')
        with self.assertRaises(ValueError):
            summary.project('tailr', value, None)

    def test_finalize_projects_only_selected_packages_and_never_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'status').mkdir()
            (root / 'status/tailr.json').write_text('null')
            cli = root / 'cli'
            cli.write_text('#!/usr/bin/env python3\nimport sys\nassert sys.argv[1] == "history"\nprint(' + repr(json.dumps([record()])) + ')\n')
            cli.chmod(0o755)
            result = summary.finalize(root, cli, ['tailr'], 'linux', 'full')
            self.assertEqual([s['package'] for s in result['suites']], ['tailr'])
            self.assertEqual(result['suites'][0]['cases'][0]['id'], 'tiny')
            self.assertFalse((root / 'exports').exists())
            self.assertLess(len(summary.encode(result)), 24000)

    def test_empty_selection_needs_no_cli_or_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(summary.finalize(root, root / 'absent', [], 'macos', 'smoke')['suites'], [])

    def test_output_cannot_inject_workflow_output_lines(self):
        value = summary.project('tailr', record(), None)
        value['cases'][0]['id'] = 'x\nforged=1'
        raw = summary.encode({'version': 1, 'platform': 'linux', 'profile': 'full', 'suites': [value]})
        self.assertNotIn('\n', raw)
        self.assertIn('\\n', raw)
