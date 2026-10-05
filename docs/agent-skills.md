# Repository agent skills

Skills live in `.agents/skills/<name>/SKILL.md` and are available to humans as
repeatable workflows as well as to agents. The [north star](north-star.md) owns
requirements, the [utility template](templates/utility.md) owns the contract shape,
and [CONTRIBUTING](../CONTRIBUTING.md) owns check commands. Skills orchestrate those
sources rather than duplicate their policies. No global installation is required.

## Choose a workflow

| Skill | Use and result |
| --- | --- |
| [clis-explore](../.agents/skills/clis-explore/SKILL.md) | Research a utility, resolve product questions, and prepare its contract and next implementation slice. |
| [clis-audit](../.agents/skills/clis-audit/SKILL.md) | Assess a utility against the standard, reporting scoped evidence, gaps, and concrete follow-ups. |
| [clis-cli-regressions](../.agents/skills/clis-cli-regressions/SKILL.md) | Turn selected behavior into hermetic CLI tests and reviewed reference fixtures. |
| [clis-verify](../.agents/skills/clis-verify/SKILL.md) | Run relevant checks and distinguish passing, failing, and unavailable verification. |
| [clis-git-delivery](../.agents/skills/clis-git-delivery/SKILL.md) | Name branches, commit a scoped diff, and publish/update a PR using the shared template. |

For example, ask an agent to “use clis-explore to prepare catr's contract” or
“use clis-audit to assess tailr and report implementation follow-ups.” Exploration
reuses prior findings; an audit can be scoped to a requirement or changed surface.
Neither request by itself authorizes implementation fixes or publication. Accepted
session decisions remain in force; skills do not add redundant approval stages.

Ordinary fixes need only the relevant workflows. A typo does not require a full
utility audit. Standards are established before utilities migrate, so an honest
audit may contain many missing or unverified requirements.

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

## Sources and maintenance

These are original repository-specific workflows. Keep skills focused on decisions
that depend on this workspace; generic debugging and review remain separate skills.
When changing a workflow, validate its frontmatter and links and try a realistic
request, including a missing-evidence or scope-boundary case. Record what was
actually exercised; structural validation alone does not establish useful behavior.

[GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow)
informs scoped branches and PR review.
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) defines commit
notation; using the same types in branch names and PR titles is a repository choice.
[GitHub's template guidance](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository)
explains the default template location. The delivery skill uses the local template;
GitHub's UI discovers it after it reaches the default branch.
