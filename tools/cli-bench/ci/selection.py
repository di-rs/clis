"""Resolve event identities once; never treat a PR merge checkout as its head."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value) or value == '0' * 40:
        raise ValueError('expected a nonzero full lowercase commit SHA')
    return value


def git(repository, *args):
    result = subprocess.run(['git', '-C', str(repository), *args], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError('Git identity resolution failed: ' + result.stderr[:1024])
    return result.stdout.strip()


def select(event_name, event, repository, fallback_sha, candidate_input='', target_input=''):
    if event_name == 'pull_request':
        if candidate_input or target_input:
            raise ValueError('PR identity cannot be overridden')
        candidate = sha(event['pull_request']['head']['sha'])
        target = sha(event['pull_request']['base']['sha'])
    elif candidate_input or target_input or event_name in ('workflow_dispatch', 'workflow_call'):
        candidate = sha(candidate_input or fallback_sha)
        if not target_input:
            raise ValueError('manual/reusable comparison requires target_sha')
        target = sha(target_input)
    elif event_name == 'push':
        if event['ref'] != 'refs/heads/' + event['repository']['default_branch']:
            raise ValueError('push must target the repository default branch')
        candidate, target = sha(event['after']), None
    else:
        raise ValueError('unsupported event')
    for value in (candidate, target):
        if value is not None and git(repository, 'rev-parse', '--verify', value + '^{commit}') != value:
            raise ValueError('SHA does not identify an exact commit')
    if target:
        previous = sha(git(repository, 'merge-base', candidate, target))
    else:
        parents = git(repository, 'rev-list', '--parents', '-n', '1', candidate).split()
        if len(parents) < 2:
            raise ValueError('candidate has no first parent; comparison unavailable')
        previous = sha(parents[1])
    return {'candidate_sha': candidate, 'target_sha': target, 'previous_sha': previous}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = select(os.environ['GITHUB_EVENT_NAME'], json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()),
                    Path.cwd(), os.environ['GITHUB_SHA'], os.environ.get('INPUT_CANDIDATE_SHA', ''),
                    os.environ.get('INPUT_TARGET_SHA', ''))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result) + '\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in result.items():
            output.write(f'{key}={value or ""}\n')


if __name__ == '__main__':
    main()
