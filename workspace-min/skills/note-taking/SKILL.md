---
name: note-taking
description: Proactively save explicit remember requests and durable user facts as structured notes.
---
# Note Taking

- Always save when the user explicitly asks to remember something.
- Auto-save only durable facts such as preferences, plans, constraints, and contacts.
- Use `note_add` for new facts, `note_update` when a saved fact is corrected, and `note_search` before duplicating.
- Give a short acknowledgement for explicit saves and important long-lived notes.
- Search existing notes before creating a new one.
- Do not auto-save one-off small talk, temporary emotions, or weak guesses.
- Prefer note tools over `memory_write` when saving long-lived user facts.
- Example durable facts: `我乳糖不耐受`, `以后默认中文回答`, `我更喜欢美式`, `六月计划去日本`.
