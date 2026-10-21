# README patterns

Choose sections around the reader's tasks. These patterns are starting points,
not a required outline, maximum length, or minimum number of pages.

## Introduce and demonstrate

Name the tool and its purpose, then show a supported first use with prerequisites,
the working directory, and an observable result. Put consequential warnings
before the command they affect. Do not promise a three-command setup when the
product requires an independently provisioned service.

For a CLI, one example can lead directly into usage and option reference. For a
service, explain the integration sequence and how to obtain its dependencies.
Only document installation methods the project actually supports.

## Organize a substantial README

Use task headings for procedures, named concepts for explanations, and tables for
parallel settings or options. Keep the main path first and advanced behavior
later. Add a short contents list when readers need to jump between major sections.

A long README can hold usage, configuration, reference, and troubleshooting when
those sections serve the same reader journey. Split deployment or contributor
material only when it becomes a distinct task with its own prerequisites.

## Avoid repetition without breaking context

Explain shared defaults once, before command-specific differences. Give each
detailed contract one canonical home. Keep short prerequisites, definitions,
and safety constraints beside the relevant action rather than making readers
follow links for every fact.

Link to applicable license, contributing, and deeper reference material when it
exists. Do not create empty documents, repeated introductions, or boilerplate
sections just to complete a template.
