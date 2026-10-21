# Agent-facing documentation

Agent instructions should record repository-specific decisions that change how
work is done: package ownership, supported verification commands, important
constraints, and where authoritative guidance lives. Keep ordinary product usage
in human-facing documentation and link to it instead of copying it.

## Repository instructions

Preserve the existing structure unless it obstructs discovery. Use concise,
actionable rules and canonical links. Do not impose a required section inventory,
external orchestration system, or output format that the repository does not use.
Distinguish mandatory rules from recommendations and preserve the user's scope.

## Skill instructions

Give each skill a precise capability and trigger description. Keep its entry point
focused on decisions and constraints that are not already obvious to a capable
agent. Add supporting references only when they serve a distinct use, and explain
when to read them. A short skill does not need a reference table or loading router.

Use available local skills and tools; do not introduce dependencies on unrelated
profiles or unavailable skills. An authorized editing task includes routine fixes,
while a review-only task leaves files unchanged. Ask about consequential missing
meaning rather than inventing requirements or demanding approval for every edit.

Use skill-creator when creating or substantially revising a skill, and validate
both its metadata and how its instructions apply to realistic requests.
