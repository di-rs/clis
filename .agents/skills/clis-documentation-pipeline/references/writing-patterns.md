# Organize documentation for readers

Use these patterns when reviewing Sanctuary READMEs and technical guides. The
reader should be able to complete a task or understand a guarantee without
assembling fragments from several pages.

## Examples worth borrowing

| Source | Useful pattern | Apply it here |
| --- | --- | --- |
| [uv: working on projects](https://docs.astral.sh/uv/guides/projects/) | Commands followed by visible results, organized by task | Start a CLI guide with one working example before its full reference |
| [uv documentation](https://docs.astral.sh/uv/) | Distinct routes for getting started, guides, concepts, and reference | Give users a clear first path and keep operations and contribution discoverable |
| [Diátaxis](https://diataxis.fr/) | Organize around learning, doing, looking up, and understanding | Give sections clear jobs; do not force every section into a separate file |
| [Google: paragraph structure](https://developers.google.com/style/paragraph-structure) | One idea per paragraph, with the important point first | Break dense explanations at changes of subject, not at arbitrary word counts |
| [Google: cross-references](https://developers.google.com/style/cross-references) | Keep essential context nearby and link selectively | Link to a detailed contract without sending readers away for a definition or two steps |

Borrow the pattern, not the site's scale, marketing voice, or navigation framework.
These sources are writing references, not evidence of Sanctuary product behavior.

## Start with a task, then explain the contract

A service's first-use path is usually a sequence: obtain credentials, submit input,
inspect the result, and clean up. Show that sequence before describing transport
internals. Keep exact fields, limits, retry behavior, and errors in named sections
where an integrator can find them.

For example, introduce Boxie with:

> Upload a ZIP, create a sandbox from that artifact, then connect a terminal.
> You need the private API URL and a consumer secret from your operator.

An explanation of SQL locks belongs later, where it can explain why a stop request
cannot race with container startup. That mechanism is useful technical content;
it does not need to be the reader's first encounter with the service.

## Explain common behavior once

Biggie's single-output commands share a destination rule. State it before the
command reference:

> Single-output commands write to `out.txt` unless you provide a filename.
> Use `-` to write to stdout. Named files are created or truncated.

Command sections then cover their differences. The `pair` command must still
explain that it needs two named files. A focused warning beside a destructive
example can be worth repeating; copying the entire output contract is not.

## Use structure to preserve technical depth

A paragraph containing memory, CPU, file-size, and lifetime limits is hard to
scan. Use a resource table for independent limits, then prose for the relationship
between deadlines, shutdown, and capacity release.

Explain causality in sentences:

> A stopped sandbox keeps its capacity slot until Docker confirms deletion.
> This prevents a failed cleanup from admitting more containers than the limit.

Keep implementation detail when it explains a guarantee, a recovery action, or a
maintenance constraint. Do not remove it merely because it is technical. Avoid
source inventories that repeat filenames without explaining responsibilities.

## Keep the page useful on GitHub and a future website

Use one descriptive title, a sensible heading hierarchy, sentence-case headings,
and language-tagged code fences. Use relative links to canonical Markdown pages
and headings. Include a short contents list when it helps with a long guide.

Keep docs in their owning packages. Do not mirror their prose into a separate
website tree. A future site can route to these sources; selecting a generator or
adding framework-specific metadata is a separate task.

Review the heading sequence and opening sentences as a reader's path. Then follow
the examples and links. A guide succeeds when readers can find, understand, and
use the information, not when it meets a word count or looks uniformly short.
