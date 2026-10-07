"""Transport tests exercise data and filesystem effects, without hosted services."""
import json
from pathlib import Path
import tempfile
import unittest

import publication


class PublicationTests(unittest.TestCase):
    def envelope(self):
        return {'schema_version': 1, 'repository': 'owner/repo', 'run_id': '123', 'run_attempt': 2,
                'platform': 'linux', 'measurement_profile': 'full', 'candidate_sha': 'a' * 40,
                'target_sha': 'b' * 40, 'previous_sha': 'c' * 40, 'requested_retention_days': 90,
                'expires_at_unix_seconds': None,
                'suites': [{'package': p, 'records': [], 'failure': {'stage': 'setup', 'message': 'tool unavailable', 'exit_code': 1}}
                           for p in ('biggie', 'tailr', 'mkdirr')], 'omissions': []}

    def history(self):
        return json.loads((Path(__file__).parent / 'fixtures/setup-history.json').read_text())[0]

    def test_shared_envelope_fixture_retains_exact_history(self):
        value = publication.decode((Path(__file__).parent / 'fixtures/setup-envelope.json').read_bytes())
        self.assertEqual(value['suites'][0]['records'][0], self.history())
        self.assertEqual(value['measurement_profile'], 'full')

    def test_sealed_failure_and_unsealed_failures_survive_roundtrip(self):
        value = self.envelope()
        record = self.history()
        value['suites'][0]['records'] = [record]
        decoded = publication.decode(publication.encode(value))
        self.assertEqual(decoded['suites'][0]['records'][0], record)
        self.assertEqual(decoded['suites'][1]['failure']['stage'], 'setup')
        self.assertIsNone(decoded['suites'][0]['records'][0]['publication']['manifest']['contract'])

    def test_missing_duplicate_and_unknown_suites_rejected(self):
        for packages in (['biggie', 'tailr'], ['biggie'] * 3, ['biggie', 'tailr', 'surprise']):
            value = self.envelope()
            value['suites'] = [{'package': p, 'records': [], 'failure': value['suites'][0]['failure']} for p in packages]
            with self.subTest(packages=packages), self.assertRaises(ValueError):
                publication.encode(value)

    def test_missing_records_cannot_be_success(self):
        value = self.envelope()
        value['suites'][0]['failure'] = None
        with self.assertRaises(ValueError):
            publication.encode(value)

    def test_unknown_fields_and_boolean_integer_rejected(self):
        for key, replacement in [('extra', 1), ('run_attempt', True), ('candidate_sha', 'HEAD'), ('measurement_profile', 'check')]:
            value = self.envelope()
            value[key] = replacement
            with self.subTest(key=key), self.assertRaises(ValueError):
                publication.encode(value)

    def test_history_profile_and_execution_kind_checked(self):
        for kind, profile in [('check-only', None), ('measure', 'smoke')]:
            value = self.envelope()
            record = self.history()
            record['publication']['manifest']['execution_kind'] = kind
            if profile:
                record['publication']['manifest']['contract'] = {'profile': profile, 'suite': {'package': 'biggie'}}
            value['suites'][0]['records'] = [record]
            with self.subTest(kind=kind, profile=profile), self.assertRaises(ValueError):
                publication.encode(value)

    def test_multiple_records_and_failed_record_without_failure_rejected(self):
        for count, failure in [(2, True), (1, False)]:
            value = self.envelope()
            value['suites'][0]['records'] = [self.history()] * count
            if not failure:
                value['suites'][0]['failure'] = None
            with self.subTest(count=count), self.assertRaises(ValueError):
                publication.encode(value)

    def test_duplicate_keys_nonfinite_and_oversize_input_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b' ' * (16 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                publication.decode(raw)

    def test_aggregate_bound_applies_to_retained_history(self):
        value = self.envelope()
        for suite in value['suites']:
            record = self.history()
            record['submitted_suite'] = 'x' * (6 * 1024 * 1024)
            suite['records'] = [record]
        with self.assertRaises(ValueError):
            publication.encode(value)

    def test_bundle_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'outside').mkdir()
            (root / 'outside/evidence').write_text('external')
            (root / 'bundle').symlink_to(root / 'outside', target_is_directory=True)
            with self.assertRaises(ValueError):
                publication.tree_bytes(root / 'bundle', 1024)

    def test_bounded_bundle_size_rejects_symlinks_and_excess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'one').write_bytes(b'12345')
            self.assertEqual(publication.tree_bytes(root, 5), 5)
            with self.assertRaises(ValueError):
                publication.tree_bytes(root, 4)
            (root / 'link').symlink_to(root / 'one')
            with self.assertRaises(ValueError):
                publication.tree_bytes(root, 10)


class FinalizationTests(unittest.TestCase):
    def test_setup_failure_writes_three_failure_entries_and_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = publication.finalize(root, root / 'missing-cli',
                                          {'repository': 'owner/repo', 'run_id': '123', 'run_attempt': 1},
                                          'smoke', 'macos', 'selection')
            self.assertEqual(status, 1)
            value = publication.decode((root / 'publication/publication.json').read_bytes())
            self.assertEqual([s['failure']['stage'] for s in value['suites']], ['selection'] * 3)
            self.assertEqual(value['measurement_profile'], 'smoke')
            self.assertIsNone(value['expires_at_unix_seconds'])
            self.assertIn('pending GitHub API', (root / 'summary.md').read_text())
            self.assertIn('| Suite | Outcome |\n| --- | --- |', (root / 'summary.md').read_text())

    def test_aggregate_overflow_retains_full_bundles_and_small_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/runs').mkdir(parents=True)
            (root / 'status').mkdir()
            (root / 'selection.json').write_text(json.dumps({'candidate_sha': 'a' * 40, 'target_sha': None, 'previous_sha': 'b' * 40}))
            for package in ('biggie', 'tailr', 'mkdirr'):
                (root / 'status' / (package + '.json')).write_text(json.dumps({'stage': 'measurement', 'message': 'fixture setup failed', 'exit_code': 1}))
            fixture = json.loads((Path(__file__).parent / 'fixtures/setup-history.json').read_text())[0]
            fixture['submitted_suite'] += '\n#' + 'x' * (6 * 1024 * 1024)
            (root / 'fixture.json').write_text(json.dumps(fixture))
            for package in ('biggie', 'tailr', 'mkdirr'):
                run = root / 'data/runs' / (package + '-retained')
                run.mkdir()
                manifest = dict(fixture['publication']['manifest'], run_id=package + '-retained')
                (run / 'manifest.json').write_text(json.dumps(manifest))
                (run / 'resolved-suite.toml').write_text(f'id = "{package}"\npackage = "{package}"\n')
            cli = root / 'fake-cli'
            cli.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
if args[0] == 'history':
    package = args[args.index('-s') + 1]
    record = json.loads((root / 'fixture.json').read_text())
    record['publication']['manifest']['run_id'] = package + '-retained'
    for field in ('submitted_suite', 'resolved_suite'):
        record[field] = record[field].replace('package = "biggie"', 'package = "' + package + '"')
    print(json.dumps([record]))
else:
    dest = Path(args[args.index('-o') + 1])
    dest.mkdir()
    (dest / 'raw.json').write_text('original evidence')
''')
            cli.chmod(0o755)
            status = publication.finalize(root, cli, {'repository': 'owner/repo', 'run_id': '123', 'run_attempt': 1}, 'full', 'linux', 'setup')
            self.assertEqual(status, 1)
            raw = (root / 'publication/publication.json').read_bytes()
            self.assertLessEqual(len(raw), 16 * 1024 * 1024)
            envelope = publication.decode(raw)
            self.assertTrue(all(entry['failure']['stage'] == 'projection' for entry in envelope['suites']))
            self.assertIn('aggregate publication exceeds 16 MiB', ' '.join(envelope['omissions']))
            for package in ('biggie', 'tailr', 'mkdirr'):
                self.assertIn(package + '-retained', ' '.join(envelope['omissions']))
                self.assertEqual((root / 'full' / package / 'raw.json').read_text(), 'original evidence')


class IndependentExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_id = 'oversize-sealed-run'
        self.run = self.root / 'data' / 'runs' / self.run_id
        self.run.mkdir(parents=True)
        self.record = json.loads((Path(__file__).parent / 'fixtures/setup-history.json').read_text())[0]
        self.record['publication']['manifest']['run_id'] = self.run_id
        (self.root / 'status').mkdir()
        (self.root / 'selection.json').write_text(json.dumps({'candidate_sha': 'a' * 40, 'target_sha': None, 'previous_sha': 'b' * 40}))
        (self.root / 'status/biggie.json').write_text(json.dumps({'stage': 'measurement', 'message': 'setup fixture failed', 'exit_code': 1}))
        self.cli = self.root / 'fake-cli'
        self.cli.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
if args[0] == 'history':
    if args[args.index('-s') + 1] == 'biggie':
        if (root / 'history-error').exists():
            sys.exit(8)
        sys.stdout.buffer.write((root / 'projection.json').read_bytes())
    else:
        print('[]')
else:
    with (root / 'exports-called.jsonl').open('a') as output:
        output.write(json.dumps(args) + '\\n')
    if (root / 'export-error').exists():
        sys.exit(9)
    destination = Path(args[args.index('-o') + 1])
    destination.mkdir()
    (destination / 'raw.json').write_text('retained sealed evidence')
''')
        self.cli.chmod(0o755)
        self.save_projection()

    def save_projection(self, oversized=False):
        if oversized:
            for field in ('submitted_suite', 'resolved_suite'):
                self.record[field] += '\n#' + '\\' * (4 * 1024 * 1024)
            target_bytes = 17_829_825
            difference = target_bytes - len(json.dumps([self.record]).encode())
            self.assertGreater(difference, 0)
            self.record['resolved_suite'] += 'x' * difference
            for field in ('submitted_suite', 'resolved_suite'):
                self.assertLess(len(self.record[field].encode()), 8 * 1024 * 1024)
            self.assertEqual(len(json.dumps([self.record]).encode()), target_bytes)
        (self.root / 'projection.json').write_text(json.dumps([self.record]))
        (self.run / 'manifest.json').write_text(json.dumps(self.record['publication']['manifest']))
        (self.run / 'resolved-suite.toml').write_text(self.record['resolved_suite'])

    def finish(self):
        status = publication.finalize(self.root, self.cli, {'repository': 'owner/repo', 'run_id': '123', 'run_attempt': 1}, 'full', 'linux', 'setup')
        self.assertEqual(status, 1)
        raw = (self.root / 'publication/publication.json').read_bytes()
        self.assertLessEqual(len(raw), 16 * 1024 * 1024)
        return publication.decode(raw)

    def test_single_oversized_history_retains_bundle_id_and_projection_failure(self):
        self.save_projection(oversized=True)
        envelope = self.finish()
        self.assertTrue(all(entry['failure']['stage'] == 'projection' for entry in envelope['suites']))
        self.assertIn(self.run_id, ' '.join(envelope['omissions']))
        self.assertIn('JSON exceeds 16 MiB', ' '.join(envelope['omissions']))
        self.assertEqual((self.root / 'full/biggie/raw.json').read_text(), 'retained sealed evidence')
        calls = [json.loads(line) for line in (self.root / 'exports-called.jsonl').read_text().splitlines()]
        self.assertEqual(calls[0][calls[0].index('-i') + 1], str(self.run))

    def test_history_command_failure_still_exports_known_run(self):
        (self.root / 'history-error').touch()
        envelope = self.finish()
        self.assertEqual(envelope['suites'][0]['failure']['stage'], 'projection')
        self.assertIn(self.run_id, ' '.join(envelope['omissions']))
        self.assertEqual((self.root / 'full/biggie/raw.json').read_text(), 'retained sealed evidence')

    def test_export_failure_does_not_mask_projection_failure_or_id(self):
        self.save_projection(oversized=True)
        (self.root / 'export-error').touch()
        envelope = self.finish()
        self.assertEqual(envelope['suites'][0]['failure']['stage'], 'projection')
        self.assertIn(self.run_id, ' '.join(envelope['omissions']))
        self.assertIn('export', ' '.join(envelope['omissions']))
        self.assertTrue((self.root / 'exports-called.jsonl').is_file())

    def test_inventory_rejects_manifest_run_id_path_mismatch(self):
        manifest = self.record['publication']['manifest']
        manifest['run_id'] = '../outside'
        (self.run / 'manifest.json').write_text(json.dumps(manifest))
        envelope = self.finish()
        self.assertFalse((self.root / 'exports-called.jsonl').exists())
        self.assertEqual(envelope['suites'][0]['failure']['stage'], 'projection')
        self.assertIn('inventory', ' '.join(envelope['omissions']))

    def test_inventory_rejects_symlinked_manifest(self):
        manifest = self.run / 'manifest.json'
        external = self.root / 'external-manifest.json'
        manifest.rename(external)
        manifest.symlink_to(external)
        envelope = self.finish()
        self.assertFalse((self.root / 'exports-called.jsonl').exists())
        self.assertIn('nonregular', ' '.join(envelope['omissions']))

    def test_inventory_rejects_symlinked_run_directory(self):
        outside = self.root / 'outside-run'
        self.run.rename(outside)
        self.run.symlink_to(outside, target_is_directory=True)
        envelope = self.finish()
        self.assertFalse((self.root / 'exports-called.jsonl').exists())
        self.assertIn('directory', ' '.join(envelope['omissions']))

    def test_inventory_rejects_symlinked_runs_parent(self):
        runs = self.run.parent
        outside = self.root / 'outside-runs'
        runs.rename(outside)
        runs.symlink_to(outside, target_is_directory=True)
        envelope = self.finish()
        self.assertFalse((self.root / 'exports-called.jsonl').exists())
        self.assertIn('inventory', ' '.join(envelope['omissions']))

    def test_inventory_rejects_symlinked_suite_file(self):
        suite = self.run / 'resolved-suite.toml'
        external = self.root / 'external-suite.toml'
        suite.rename(external)
        suite.symlink_to(external)
        envelope = self.finish()
        self.assertFalse((self.root / 'exports-called.jsonl').exists())
        self.assertIn('nonregular', ' '.join(envelope['omissions']))

    def test_inventory_failure_still_writes_bounded_failure_envelope(self):
        (self.run / 'manifest.json').unlink()
        envelope = self.finish()
        self.assertTrue(all(entry['failure']['stage'] == 'projection' for entry in envelope['suites']))
        self.assertIn('inventory unavailable', ' '.join(envelope['omissions']))
        self.assertFalse((self.root / 'exports-called.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
