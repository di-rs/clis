---
name: clis-documentation-pipeline
description: Use when creating or editing persistent, human-facing technical documentation about CLIs, tools, products, or architecture, including READMEs, tutorials, how-to guides, references, troubleshooting, and architecture overviews. Also use when reviewing these documents. Excludes ordinary chat answers, agent instructions, skill authoring, and temporary working notes.
---

# CLIs documentation pipeline

Produce a saved document that helps its intended reader use or understand the
system. Run the three repository skills in order on the same document, keeping
the reader's goal and technical facts intact through each pass.

## Establish the document's job

Infer the reader, their situation, the question the document answers, and the
destination from the request and nearby documentation. Ask only when missing
information would change the result. Update the canonical document in place;
create a new page when it serves a distinct reader need. Keep each CLI's docs
inside its owning package, as required by [AGENTS.md](../../../AGENTS.md).

Choose the main Diátaxis mode: tutorial, how-to, reference, or explanation. An
architecture overview is usually explanation. A README introduces the project
and routes readers to deeper material. Match depth to the requested document;
a small CLI needs a smaller page than a multi-service product.

Inspect the implementation, manifests, tests, existing docs, and relevant specs
before asserting behavior. Distinguish shipped behavior from proposals. Keep
unresolved facts visible as questions for the author, rather than inventing
commands, installation methods, platform support, or guarantees.

## Run the passes

Read each skill from the local path below when its pass begins. Resolve its
supporting references relative to that skill's directory. These are reading and
editing instructions; no slash-command runtime or installation is needed.

For review-only work, use all three passes as review lenses: record proposed
changes and evidence in the memo, keeping every source document unchanged.

### 1. Draft with write-docs

Read [write-docs](../write-docs/SKILL.md). Establish context at the point of need,
choose a useful sequence, and write in plain language for the intended reader.
Use prose for causes and relationships, steps for procedures, and tables or
lists for parallel reference material. Introduce unfamiliar terms when first
used. Include concrete examples that help the reader recognize success.

Treat upstream README outlines and length targets as guidance for this
document's scope. Include sections, status claims, and links only when relevant
and supported. References to other Flagrare skills are optional context, not
dependencies of this pipeline. The local editorial pass runs at step 3.

### 2. Check with technical-documentation

Read [technical-documentation](../technical-documentation/SKILL.md) and only the
references relevant to the surface being written. Check the draft against the
actual system and correct it before the editorial pass.

| Surface | Check within the document's scope |
| --- | --- |
| CLI or tool guide | Supported installation/build path, working directory, prerequisites, arguments and options, defaults, stdin/stdout/stderr, exit behavior, configuration, and useful examples. |
| Product or API documentation | User workflow, prerequisites, authentication, inputs, outputs, configuration, observable failures, recovery, and supported limitations. |
| Architecture overview | Purpose, component responsibilities and boundaries, data/control flow, external dependencies, constraints, and reasons for consequential trade-offs. Link details that belong elsewhere. |

Completeness applies to the interface or task the page promises to cover. A
reference covers every entry in that scope; a guide explains its task and links
to the reference. Preserve the explanation and causality an architecture reader
needs, rather than turning the overview into an inventory of internals.

Use source code and tests to establish behavior; templates describe presentation,
not what the product implements. In particular, verify actual exit codes,
configuration precedence, and available flags instead of borrowing conventions
from examples. Run documented commands in a temporary workspace where practical,
check their outputs, and report anything that could not be verified. Do not
change product behavior to make a documentation example true.

### 3. Edit with editorial-pass

Read [editorial-pass](../editorial-pass/SKILL.md). Read the whole draft twice,
identify its central message, and review structure, sequencing, pacing,
consistency, and clarity before individual sentences. Remove writing-process
residue while preserving facts, useful caveats, examples, and the author's voice.

Prepare the skill's editorial memo before applying revisions. When the user has
asked to write, edit, or polish the document, that authorization includes these
editorial fixes: apply them as part of the task without another approval round.
For a review-only request, deliver the memo and leave the document unchanged.
Ask about an unresolved meaning or consequential author decision when it affects
correctness; continue fixes that do not depend on that answer. The memo can stay
in the conversation or working notes; save it only when requested.

## Verify and deliver

Recheck claims changed by editing against their sources, then read the saved
document from beginning to end. Ensure local links resolve from its final path
and diagrams, if useful, describe the system accurately. Keep the verification
trail in the completion report, with reader-relevant evidence in the document.

Follow [Testing](../../../docs/testing.md) for documentation checks. Pass new and
changed Markdown paths to `prek run lychee --files ... --show-diff-on-failure`
and `prek run typos --files ... --show-diff-on-failure`, then run `git diff --check`.
Run additional checks only when needed for the documented examples or changed
area. Report missing tools or unverified commands accurately.

Deliver the saved document paths, a brief description of the result, verification
performed, and any unresolved questions. For review-only work, deliver the memo.
The final artifact should stand on its own for someone who never saw the chat.

Example invocation: "Use $clis-documentation-pipeline to update
coreutils/echor/README.md for new users, with verified examples and exit behavior."

For upstream versions, licenses, local adaptations, and updates, read
[upstream sources](references/upstream.md) when maintaining these skills.
