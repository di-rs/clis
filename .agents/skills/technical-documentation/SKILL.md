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

## Responsibility

Verify that documentation helps readers complete the task it promises. Check
interfaces and architecture against implementation, and distinguish future design
from current behavior. Do not invent architecture decisions or broaden the task
into product redesign. Use the writing and editorial skills for prose quality.

## Reference Files

| Reference | When to load |
|-----------|-------------|
| `references/readme-patterns.md` | Writing or restructuring a README — reader-driven sections, quickstart patterns, installation variants, configuration documentation |
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

**Use examples where they resolve uncertainty.** Show complete request/response or configuration examples when a reader needs them to use the interface correctly. Do not repeat examples mechanically for every concept or option.

## Portability

This skill is intentionally host-neutral. Use your agent's normal mechanisms to load the references, templates, and scripts listed here. Do not assume a particular profile system, task orchestrator, memory service, or response-handoff format.
