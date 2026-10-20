---
name: gitmoji-commit
description: Use when creating commits or choosing pull request titles in this repository.
---

- Every commit subject and PR title must start with a relevant [Gitmoji](https://gitmoji.dev/) emoji or shortcode, a space, and a concise summary.
- Examples: `✨ Add feature`, `:bug: Fix crash`, `📝 Update docs`.
- Keep normal commit hooks and Gitmoji validation enabled. Use `prek install` only if hooks need installation. Never run prek checks manually; let the actual commit invoke them once.
- Optional interactive picker: `brew install gitmoji`, then `gitmoji -c`; repository preferences are in `.gitmojirc.json`.
