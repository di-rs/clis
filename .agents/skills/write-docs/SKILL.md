---
name: write-docs
description: Write or restructure human-facing technical documentation, including READMEs, guides, architecture explanations, and reference pages. Use for prose and organization; use technical-documentation to verify contracts and editorial-pass to review the finished document.
---

# Write docs

Write for someone who knows the field but has not explored this repository.
Start with what they need to understand or accomplish. Name the product and its
purpose before asking them to install or configure it.

## Build the explanation

Establish the reader's goal and the page's promise from the request and nearby
docs. Introduce unfamiliar concepts where they first matter. Explain causes and
relationships in connected prose: readers should understand why a step is needed,
not reconstruct the reasoning from a list of disconnected facts.

Use steps for procedures, tables for comparisons and settings, and bullets for
parallel items. Give each section a clear job. Diátaxis modes (tutorial, how-to,
reference, explanation) help distinguish those jobs, but do not require a separate
file for each mode. Keep related tasks together when splitting would force readers
to follow links back and forth.

Lead with useful facts, then provide the supporting mechanism. Prefer concrete
examples over abstract claims. Include prerequisites, working directories, expected
results, and consequential warnings alongside the action they affect. Never invent
an installation method, command, guarantee, or stability claim to fill a template.

## Shape a README

A README introduces the component, provides a supported first-use path, and points
to deeper material. It may also contain substantial usage and reference sections.
There is no length target or mandatory heading inventory. Use descriptive headings
and a short contents list when they improve navigation.

Separate a page when its task or audience warrants a distinct destination. Keep
one canonical explanation of each detailed contract; brief local context and
necessary safety prerequisites can recur. Preserve useful depth, examples,
limitations, and tutorial credits when consolidating.

## Make the prose easy to follow

Use familiar words, active verbs, and consistent names. Prefer a direct statement
to performed expertise or forced casualness. Match the tone to the reader's task:
a first-use guide can be patient; recovery instructions should be decisive.

Avoid writing-process narration, generic introductions such as "this document
describes," and repeated background. A technical explanation should explain what
happens and why without recounting how the author investigated it.

Link to source and related docs where readers need to inspect or act. Use relative
links for maintained repository documentation; pin external code when a particular
version is essential. Add a diagram, screenshot, or example when it materially
clarifies a flow or behavior. Do not require an illustration for every named item,
and do not present invented screenshots as evidence.

## Check the draft

Read headings in order, then the full page. Check that concepts arrive before
readers need them, dense passages have enough explanation, and reference sections
remain easy to scan. Read related pages for repeated contracts and fragmented tasks.

Use [technical-documentation](../technical-documentation/SKILL.md) for factual and
interface checks, then [editorial-pass](../editorial-pass/SKILL.md) for whole-document
editing. In Sanctuary, the [documentation pipeline](../clis-documentation-pipeline/SKILL.md)
owns repository layout and verification conventions.

This skill is locally adapted from the sources recorded in
[upstream attribution](../clis-documentation-pipeline/references/upstream.md).
