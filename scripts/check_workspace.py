#!/usr/bin/env python3
"""Check resolved member licenses and compiler-lint inheritance."""
import json
from pathlib import Path
import subprocess
import sys
import tomllib


def check_workspace(root: Path, metadata: dict) -> list[str]:
    errors = []
    manifest = tomllib.loads((root / 'Cargo.toml').read_text())
    if manifest.get('workspace', {}).get('lints', {}).get('rust', {}).get('unsafe_code') != 'forbid':
        errors.append('Cargo.toml: workspace.lints.rust.unsafe_code must be forbid')
    members = set(metadata['workspace_members'])
    for package in metadata['packages']:
        if package['id'] not in members:
            continue
        path = Path(package['manifest_path'])
        member = tomllib.loads(path.read_text())
        if member.get('lints', {}).get('workspace') is not True:
            errors.append(f'{package["name"]} ({path}): lints.workspace must be true')
        if not package.get('license'):
            errors.append(f'{package["name"]} ({path}): license must be declared or inherited')
    return errors


def main() -> int:
    try:
        result = subprocess.run(['cargo', 'metadata', '--locked', '--no-deps',
                                 '--format-version', '1'], check=True, capture_output=True, text=True)
        metadata = json.loads(result.stdout)
        errors = check_workspace(Path.cwd(), metadata)
        if errors:
            print('\n'.join(errors), file=sys.stderr)
            return 1
        print(f'Workspace policy: {len(metadata["workspace_members"])} members pass')
        return 0
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        return error.returncode if error.returncode > 0 else 1
    except (OSError, ValueError, KeyError) as error:
        print(f'Workspace policy could not be checked: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
