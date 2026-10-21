---
name: technical-documentation
description: Create and review technical documentation, including READMEs, agent-facing instructions, API references, and CLI help. Use when documentation must help someone complete real work. Do not use for marketing copy, brand messaging, or long-form editorial content.
license: MIT
metadata:
  source_repo: https://github.com/magnus919/hermes-profiles
  source_commit: 867a555
---


# Technical Documentation Methodology

Technical documentation is the interface between a tool and its user. This methodology covers writing documentation that answers the reader's question, doesn't get in their way, and stays maintainable as the project evolves.

## The Technical Writer's Domain

| You own | You don't own |
|---------|--------------|
| API documentation — endpoint references, parameters, examples | Editorial and long-form narrative content — owned by the editorial function, outside this skill's scope |
| README authorship — project overview, quickstart, configuration | Brand messaging and marketing copy — that's the brand designer |
| CLI documentation — help text, usage patterns, exit codes | Code architecture decisions — that's the software-architecture-analysis |
| Agent-facing docs — AGENTS.md, skill documentation | Project roadmap and product vision — that's the product manager |
| Reference documentation — config files, troubleshooting, architecture | Performance optimization guidance — that's the systematic-debugging / SRE |
| Documentation site structure — IA, cross-referencing, search | Code quality and standards — that's the qa-methodology / QA engineer |

## Reference Files

| Reference | When to load |
|-----------|-------------|
| `references/readme-patterns.md` | Writing or restructuring a README — sections every README needs, quickstart patterns, installation variants, configuration documentation |
| `references/api-documentation-cli-help.md` | Documenting APIs (endpoint structure, parameter conventions, example design, auth patterns, OpenAPI annotations) and designing CLI help text (usage line, flags, exit codes, subcommand hierarchy, man pages) |
| `references/agent-facing-docs.md` | Writing AGENTS.md and skill documentation — trigger patterns, loading order, output contracts, cross-references |

For documentation site structure, use the core principles below: organize around reader tasks, link overviews to deeper pages, and keep one canonical page per concept. The upstream `references/information-architecture.md` is not included in the source distribution.

## Related Skills

- For office-format deliverables (PDF, Word, Excel, PowerPoint), use a format-specific skill if one is available. Doc-site and markup conventions stay here.

## Core Principles

**Match detail to the reader’s task.** Explain interfaces through inputs, outputs, examples, and observable behavior. Retain internal mechanisms when readers need them to understand guarantees, debug failures, operate the system, or extend it. Organize them separately from first-use instructions.

**Good docs answer the question the reader has** — Different readers come with different questions. Getting started? Reference? Troubleshooting? Structure docs to route each reader to their answer fast.

**Be complete within the page’s promise.** A reference covers the parameters and behavior it promises to document. A guide gives readers everything needed to finish its task, with links to deeper reference. Do not turn every introduction into an exhaustive catalog.

**Maintain one explanation of each detailed contract.** Keep closely related material together. Use headings to make long pages navigable, and separate pages only when they improve a reader’s path. Repeat brief context when needed, but avoid parallel versions of the same rules.

**Show, don't just tell** — Every concept needs a worked example. Every API endpoint needs a request and response. Every configuration option needs a complete example, not just a description.

## Portability

This skill is intentionally host-neutral. Use your agent's normal mechanisms to load the references, templates, and scripts listed here. Do not assume a particular profile system, task orchestrator, memory service, or response-handoff format.
