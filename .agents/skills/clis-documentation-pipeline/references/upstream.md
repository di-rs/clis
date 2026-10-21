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

The repository pipeline owns Sanctuary's documentation layout, verification, and
writing/checking/editing sequence. The root README remains brief, component docs
have flat topic locations, and future direction is separate from current architecture.

The writing and editorial entry points are condensed local adaptations. They retain
reader context, coherent explanations, whole-document review, and cross-page
consolidation. Unavailable external skills, unrelated upstream anecdotes, fixed
illustration requirements, and conflicting approval gates have been removed.
Authorized edits include editorial revisions; review-only work remains read-only.

Technical completeness is scoped to the page's promise. README references impose
no length or section quota. Examples and visuals serve a reader's need rather than
an exhaustive template. Source attribution and the technical-documentation license
remain preserved. Its missing upstream information-architecture reference is
replaced with inline guidance, and it does not require unavailable role skills.

The technical references retain earlier local corrections to Markdown fences,
OpenAPI parameter serialization and component maps, FastAPI imports, and CLI exit
code examples. The OAuth example uses a refresh-token placeholder; the historical
false-positive finding remains fingerprinted in `.gitleaksignore`.

## Update deliberately

Inspect a new upstream commit before replacing a skill. Import the selected skill
directory and its referenced resources, preserve license notices, then reapply or
reassess the adaptations above. Update the pinned links in this file together.

Validate skill frontmatter and local references, run the Markdown checks from
[the documentation pipeline](../SKILL.md#verify-and-deliver) on every imported Markdown file, and try the
pipeline on a small real document. Check that it uses repository-local skills in
the selected order, verifies examples, completes authorized edits, and keeps
review-only work read-only.
