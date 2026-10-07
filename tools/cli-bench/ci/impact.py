"""Select benchmark-enabled CLIs from both exact committed dependency graphs.

No Cargo invocation, source checkout, dataset generation, or build is needed.
Only normal/build path dependencies (including target-specific and inherited
workspace dependencies) propagate impact; dev dependencies do not affect roles.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import tomllib

from selection import git, sha

PACKAGES = ('biggie', 'tailr', 'mkdirr')


def override(value, event, workflow_ref='', repository=''):
    if not value:
        return None
    # Reusable jobs keep their caller's event and workflow identity. Match the
    # same direct-workflow boundary used by the comment job; no input controls it.
    direct = f'{repository}/.github/workflows/cli-bench.yml@'
    reusable = bool(repository and workflow_ref and not workflow_ref.startswith(direct))
    if event != 'workflow_dispatch' and not reusable:
        raise ValueError('package override requires a manual/reusable event')
    names = value.split(',')
    if len(names) != len(set(names)) or any(name not in PACKAGES for name in names):
        raise ValueError('expected comma-separated benchmark package names')
    return [package for package in PACKAGES if package in names]


def graph(repository, revision):
    files = git(repository, 'ls-tree', '-r', '--name-only', '-z', revision).rstrip('\0').split('\0')
    root = tomllib.loads(git(repository, 'show', revision + ':Cargo.toml'))
    inherited = root.get('workspace', {}).get('dependencies', {})
    packages = {}
    for path in files:
        if not path.endswith('/Cargo.toml'):
            continue
        manifest = tomllib.loads(git(repository, 'show', revision + ':' + path))
        if 'package' not in manifest:
            continue
        directory = str(PurePosixPath(path).parent)
        dependencies = []
        tables = [manifest, *manifest.get('target', {}).values()]
        for table in tables:
            for kind in ('dependencies', 'build-dependencies'):
                for name, dependency in table.get(kind, {}).items():
                    base = directory
                    if isinstance(dependency, dict) and dependency.get('workspace'):
                        dependency = inherited.get(name, {})
                        base = ''
                    if isinstance(dependency, dict) and 'path' in dependency:
                        dependencies.append(posixpath.normpath(posixpath.join(base, dependency['path'])))
        sources = [directory + '/src/']
        build = manifest['package'].get('build', 'build.rs')
        exact = {path, directory + '/benches/cli-bench.toml'}
        if isinstance(build, str):
            exact.add(posixpath.normpath(directory + '/' + build))
        for target in [manifest.get('lib', {}), *manifest.get('bin', [])]:
            if 'path' in target:
                exact.add(posixpath.normpath(directory + '/' + target['path']))
        packages[directory] = {'name': manifest['package']['name'], 'dependencies': dependencies,
                               'sources': sources, 'exact': exact}
    return packages


def affected(repository, previous, candidate):
    sha(previous)
    sha(candidate)
    # --no-renames exposes both old and new paths, also covering deletes.
    paths = set(git(repository, 'diff', '--no-renames', '--name-only', '-z', previous, candidate, '--').rstrip('\0').split('\0'))
    if any(path in ('Cargo.toml', 'Cargo.lock', 'rust-toolchain', 'rust-toolchain.toml', '.cargo/config', '.cargo/config.toml') for path in paths):
        return list(PACKAGES)
    selected = set()
    for revision in (previous, candidate):
        packages = graph(repository, revision)
        changed = {directory for directory, data in packages.items()
                   if data['name'] != 'cli-bench' and any(path in data['exact'] or any(path.startswith(prefix) for prefix in data['sources']) for path in paths)}
        while True:
            expanded = changed | {directory for directory, data in packages.items() if any(dep in changed for dep in data['dependencies'])}
            if expanded == changed:
                break
            changed = expanded
        selected.update(packages[directory]['name'] for directory in changed)
    return [package for package in PACKAGES if package in selected]


def range_start(event_name, event, selection):
    """Impact since the last PR push is separate from the measured merge-base."""
    if event_name == 'pull_request' and event.get('action') == 'synchronize':
        before = sha(event.get('before'))
        if sha(event.get('after')) != selection['candidate_sha']:
            raise ValueError('synchronize after SHA differs from candidate')
        return before
    return selection['previous_sha']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text())
    packages = override(os.environ.get('INPUT_PACKAGES', ''), os.environ['GITHUB_EVENT_NAME'],
                        os.environ.get('GITHUB_WORKFLOW_REF', ''), os.environ.get('GITHUB_REPOSITORY', ''))
    if packages is None:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        previous = range_start(os.environ['GITHUB_EVENT_NAME'], event, selection)
        packages = affected(Path.cwd(), previous, selection['candidate_sha'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('packages=' + json.dumps(packages, separators=(',', ':')) + '\n')
        output.write('has_packages=' + str(bool(packages)).lower() + '\n')


if __name__ == '__main__':
    main()
