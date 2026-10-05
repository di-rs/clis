# Repository agent skills

Skills live in `.agents/skills/<name>/SKILL.md`. Each has a focused description
for discovery and self-contained instructions. They reuse `AGENTS.md`, the root
README, and `prek.toml` as the sources of repository policy and check commands.
No scripts, new dependencies, or global skill installations are needed.

## Included

| Skill | Use |
| --- | --- |
| [clis-git-delivery](../.agents/skills/clis-git-delivery/SKILL.md) | Name branches, commit a scoped diff, and publish or update a PR using the repository template. |
| [clis-verify](../.agents/skills/clis-verify/SKILL.md) | Establish a baseline, run package checks before workspace checks, and report evidence and limitations. |
| [clis-cli-regressions](../.agents/skills/clis-cli-regressions/SKILL.md) | Turn supported CLI behavior into deterministic integration tests, with byte assertions and isolated filesystem fixtures. |

## Git conventions

- Branches: `<type>/<short-description>`, e.g. `fix/tailr-unicode-output` or
  `chore/repository-agent-skills`. Use lowercase, hyphenated descriptions.
- Commits and PR titles: `<type>(<scope>): <description>`, e.g.
  `fix(tailr): preserve Unicode output`. Scope is optional for workspace changes.
- Types: `feat`, `fix`, `chore`, `perf`, `docs`, `test`, `refactor`, `build`, `ci`,
  `style`, and `revert`. Choose the type that describes the actual change.
- PR bodies: fill [the shared template](../.github/pull_request_template.md) for
  every PR, including those created through the CLI.
- Resolve the actual default branch rather than assuming its name. It is
  currently `master`.

## Research and further skills

Reviewed 2026-10-05. These are original repository-specific instructions, not
vendored third-party skill packages. This note records the research; agents do
not need to read it or fetch its sources during routine skill use.

[GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow)
supports short descriptive branches, focused commits, and PR review.
[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
defines commit types, optional scopes, and breaking-change notation. The branch
format and matching PR-title format above are our chosen conventions; the
specification governs commit messages.
[GitHub's template guidance](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository)
establishes `.github/pull_request_template.md` as a default PR template. It becomes
available in GitHub's PR interface after it is committed to the remote default
branch. The local delivery skill uses it immediately.

Other candidates assessed against this workspace:

| Candidate | Decision |
| --- | --- |
| Rust workspace verification | Included: nightly, nextest, and strict workspace Clippy make the exact check sequence useful across every app. |
| CLI regression testing | Included: byte boundaries, stdin, permissions, and GNU/BSD differences require decisions beyond generic test advice. |
| README maintenance | Keep in `AGENTS.md`: the app documentation contract is already concise; a separate skill would largely duplicate it. |
| Systematic debugging and code review | Keep as general user-level skills for now; add a local workflow if recurring repository-specific gaps appear. |
| [GitHub CI repair](https://github.com/openai/skills/tree/main/skills/.curated/gh-fix-ci) | Optional now that a PR workflow exists: inspect failing GitHub Actions logs when local checks cannot reproduce a failure. The official skill has additional approval steps; review before adopting. |
| [PR review comments](https://github.com/openai/skills/tree/main/skills/.curated/gh-address-comments) | Optional later: useful with recurring PR reviews; requires authenticated `gh` and selection of comments to address. Review its instructions before adopting. |
| Benchmarks | Defer: repository guidance excludes new benchmark work from tests-and-documentation tasks. |

The two external candidates were inspected at their original source. They have
not been installed or copied into this repo. Generic Rust best-practice packs,
deployment skills, and automatic release skills are not essential to this setup.
