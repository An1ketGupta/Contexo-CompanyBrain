---
title: Using prompt templates
category: Features
order: 5
tags: [templates, prompts, shortcuts, snippets, library]
---

Templates are reusable prompts that capture how your team wants to phrase a recurring task — writing a launch email, drafting a job description, summarizing a customer call. Anyone in the workspace can run them; admins can manage who shares what.

## Run a template

1. In the chat input, type `/` to open the template picker.
2. Start typing to filter by name or tag.
3. Select a template. If it has variables (e.g., `{{role}}`, `{{tone}}`), a small form will appear before the prompt is sent.
4. Fill the fields and click **Run**.

The composed prompt is sent to chat exactly as if you'd typed it yourself, with all your company's documents available as context.

## Create your own

1. Go to **Settings → Templates**.
2. Click **New template**.
3. Give it a name, an optional description, and the prompt body. Anywhere you write `{{variable_name}}` becomes a fill-in slot when the template runs.
4. Pick a **visibility**:
   - **Personal** — only you can see and run it.
   - **Workspace** — every member can see and run it.
5. Save.

## A worked example

A "Write a launch email" template might look like:

```
Write a launch email for {{product_name}}.
Audience: {{audience}}.
Tone: {{tone}}.
The email should reference our brand voice and recent release notes.
```

When run, the user picks the product name, audience, and tone, and the model writes the email using your brand-voice docs and release notes for context.

## Editing and versioning

Edits are immediate — there's no draft/publish step. If a template is shared with the workspace, your edit takes effect for the next run by anyone. Past chat messages keep their original prompt text so history is never rewritten.

## Tips

- Keep prompts under ~200 words. Long preambles crowd out room for the model's reasoning.
- Phrase variables as nouns, not full sentences (`{{tone}}` not `{{what_tone_should_we_use}}`).
- Use templates to enforce structure ("Return a 5-line summary, then a bulleted list of next actions") so outputs are predictable.
