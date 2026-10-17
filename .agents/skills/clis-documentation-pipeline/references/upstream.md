# Upstream documentation skills

The three pipeline dependencies are checked into `.agents/skills/` so a checkout
contains the instructions and supporting references. No global installation or
Node.js runtime is required to use them. The `clis-documentation-pipeline` skill is
repository-owned.

## Sources

| Local skill | Upstream directory at the imported commit | License |
| --- | --- | --- |
| [write-docs](../../write-docs/SKILL.md) | [Flagrare/agent-skills, plugins/flagrare/skills/write-docs](https://github.com/Flagrare/agent-skills/tree/ddc0bef8cf00f13ae65a4ad069bc040a0dad386a/plugins/flagrare/skills/write-docs) | MIT, declared by the plugin metadata. |
| [technical-documentation](../../technical-documentation/SKILL.md) | [magnus919/agent-skills, technical-documentation](https://github.com/magnus919/agent-skills/tree/06ca706ecaa80f6afb31ae28eb160d39372b7267/technical-documentation) | MIT; [license and copyright notice](../../technical-documentation/LICENSE.md) copied from the repository root. |
| [editorial-pass](../../editorial-pass/SKILL.md) | [Flagrare/agent-skills, plugins/flagrare/skills/editorial-pass](https://github.com/Flagrare/agent-skills/tree/ddc0bef8cf00f13ae65a4ad069bc040a0dad386a/plugins/flagrare/skills/editorial-pass) | MIT, declared by the plugin metadata. |

Flagrare identifies Yuri Flagrare as the author and declares MIT in its
[plugin metadata](https://github.com/Flagrare/agent-skills/blob/ddc0bef8cf00f13ae65a4ad069bc040a0dad386a/plugins/flagrare/.claude-plugin/plugin.json).
That snapshot contains no separate license file. No upstream copyright notice
has been replaced with this repository's notice.

The technical-documentation skill's existing metadata and source index describe
its earlier extraction from `magnus919/hermes-profiles`. They are preserved;
the table above identifies the subsequent distribution imported here. Its README,
references, and evaluation cases are included.

## Local adaptations

- Both Flagrare skills link to their installed writing/editorial counterpart
  instead of requiring a Flagrare slash command. References to other Flagrare
  skills remain upstream context; the wrapper does not require them.
- `write-docs` uses "Valve game patch notes" in two upstream asides so the
  repository spelling check does not mistake the game's name for a typo.
- `editorial-pass` has a shorter discovery description within the 1,024-character
  limit. Its research link points to the pinned upstream file, since that file
  lives outside the imported skill directory.
- `technical-documentation` no longer points to an uninstalled `documents` skill.
  Its missing `information-architecture.md` reference is replaced with inline
  routing guidance; upstream does not ship that file. A redundant compatibility
  frontmatter field was removed for compatibility with the skill validator.
- Nested Markdown examples in `readme-patterns.md` use four-backtick outer fences
  so their inner shell blocks render correctly.
- Trailing whitespace was removed from blank lines in the API/CLI reference.
- The OAuth response uses an explicit refresh-token placeholder instead of a
  credential-like example value. The original import's false-positive finding is
  fingerprinted in `.gitleaksignore` so historical scans remain enabled.

The wrapper keeps the selected order, adapts completeness to the page's purpose,
and applies editorial changes within an authorized writing/editing task. The
standalone upstream editorial skill retains its memo-before-rewrite workflow.

## Update deliberately

Inspect a new upstream commit before replacing a skill. Import the selected skill
directory and its referenced resources, preserve license notices, then reapply or
reassess the adaptations above. Update the pinned links in this file and the
editorial research link together.

Validate skill frontmatter and local references, run the Markdown checks from
[Testing](../../../../docs/testing.md) on every imported Markdown file, and try the
pipeline on a small real document. Check that it uses repository-local skills in
the selected order, verifies examples, completes authorized edits, and keeps
review-only work read-only.
