---
name: clis-git-delivery
description: Use when naming a branch, committing changes, or opening or updating a GitHub pull request in the clis workspace.
---

# Git delivery

Read the root `AGENTS.md` and the relevant check commands in `README.md` and
`prek.toml`. Execute the requested parts of the workflow. A commit request alone
stays local; opening a PR includes pushing its feature branch. For local review,
leave changes uncommitted on the requested branch, including the default branch.

## Branches and messages

Inspect the current branch, remotes, working diff, staged diff, and untracked
files. Resolve the base from the user's target, otherwise the remote default:
`git symbolic-ref --short refs/remotes/origin/HEAD`. If unavailable or stale,
query `gh repo view --json defaultBranchRef`; do not assume `main` or `master`.

New branch names use `<type>/<short-description>`, e.g.
`fix/tailr-unicode-output`, `feat/grepr-line-numbers`, or
`chore/repository-agent-skills`. Use lowercase, hyphenated descriptions.
Commit messages and PR titles use `<type>(<scope>): <description>`, e.g.
`fix(tailr): preserve Unicode output`. Scope is the app or area; omit it for
workspace-wide changes. Use imperative, specific descriptions.

| Type | Change |
| --- | --- |
| `feat`, `fix` | New capability, bug fix |
| `perf`, `refactor` | Performance improvement, restructuring without behavior changes |
| `docs`, `test`, `style` | Documentation, tests, formatting |
| `build`, `ci`, `chore`, `revert` | Build/dependencies, CI, maintenance, reversal |

Honor explicit user naming and reuse the task's existing branch. Validate new
names with `git check-ref-format --branch '<name>'` and check for collisions.
For new work, branch from the intended base. For an existing fix on the default
branch, branch from its current HEAD to preserve the fix. Resolve detached HEAD
or an unclear base without implicitly resetting or stashing the user's work.
Mark actual breaking commits with `!` or a `BREAKING CHANGE:` footer.

## Commit

Run relevant package and workspace checks; distinguish baseline failures from
regressions. Review and stage explicit task paths or hunks. Plain `git commit`
includes everything staged: isolate the commit if unrelated changes are already
in the index, preserving those staged and working changes. When entire selected
files contain only task changes, `git commit --only -- <paths>` excludes other
staged paths. For mixed files, isolate task hunks with a temporary index instead.
Inspect the resulting commit's file list and remaining status. Keep commits focused; do not blanket
stage, silently bypass failed hooks, or amend someone else's commit.

## Pull request

Check `gh` availability/authentication and review the complete base-to-head diff
and commit list. Fill `.github/pull_request_template.md` for every PR. Keep its
Summary, Validation, and Notes sections; report actual commands and outcomes,
including skipped checks and existing failures. Write the body to a temporary
file with real newlines and pass `--body-file`.

Query existing PRs by repository, head owner/branch, and base before creating.
Reuse a matching open PR; update it only when requested and preserve its review state.
If none exists, push the confirmed feature branch, then use `gh pr create` with
explicit `--repo`, `--base`, `--head`, `--title`, and `--body-file` values. Use
`--draft` for incomplete work or unresolved checks; honor a requested review state.
Do not use `--dry-run` for local review because it may push.

After an ambiguous creation error, query matching PRs with `--state all` before
retrying. Closed or merged matches establish prior creation: report their state
rather than automatically reopening or creating another PR.
A failed lookup does not prove absence; stop mutations if the outcome
remains unknown. Verify the PR's head/base and URL. Attach the PR with
`attach_artifact` when available. Return its URL, commit ID, and check results.
Merge, force-push, branch deletion, reviewer requests, and comments are separate
operations requiring task authorization.
