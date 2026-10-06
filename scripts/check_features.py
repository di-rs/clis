#!/usr/bin/env python3
"""Run full-feature behavior tests only when workspace members expose features."""
import json
import subprocess
import sys


def feature_members(metadata: dict) -> list[str]:
    members = set(metadata['workspace_members'])
    return sorted(package['name'] for package in metadata['packages']
                  if package['id'] in members and package['features'])


def main() -> int:
    try:
        result = subprocess.run(
            ['cargo', 'metadata', '--locked', '--no-deps', '--format-version', '1'],
            check=True, capture_output=True, text=True)
        members = feature_members(json.loads(result.stdout))
        print('Member features:', ', '.join(members) or 'none', flush=True)
        if members:
            subprocess.run(['cargo', 'nextest', 'run', '--locked', '--workspace',
                            '--all-features', '--profile', 'ci'], check=True)
            subprocess.run(['cargo', 'test', '--locked', '--workspace',
                            '--all-features', '--doc'], check=True)
        return 0
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        return error.returncode if error.returncode > 0 else 1


if __name__ == '__main__':
    sys.exit(main())
