---
id: prd-mvp
name: PRD / MVP scoping
description: |
  Turn a fuzzy product idea into a one-page PRD with a shippable MVP
  scope, acceptance criteria, and a 1-2 week build plan.
version: 0.1.0
tags: [product, prd, mvp, scoping, planning]
agents: ["*"]
triggers:
  - prd
  - mvp
  - user story
  - scope
  - acceptance criteria
  - product spec
inputModes: [text/plain]
outputModes: [text/markdown, text/plain]
mcp_servers: []
examples:
  - "Draft a PRD for a CLI that lints OpenAPI specs."
  - "What is the MVP scope for an internal status page?"
  - "Write user stories with acceptance criteria for feature X."
securityRequirements: []
---

# PRD / MVP scoping

## Purpose

Compress a vague request ("we should build a status page") into a
one-page document a team can start building from on Monday.

## When to use

- The user asks for a PRD, MVP scope, or "what should we build first".
- The request is broad enough that direct implementation would be
  premature.

## Inputs

- The product idea in 1-3 sentences.
- Optional: target user, deadline, available headcount.

## Procedure

1. **Restate the problem** in one sentence the user agrees with.
2. **Identify the user persona** and one primary job-to-be-done.
3. **List candidate features**, then ruthlessly cut to the smallest
   set that delivers the JTBD. Anything cut goes into "v2".
4. **Write user stories** in the form
   `As <persona>, I want <action>, so that <benefit>`.
5. **Add acceptance criteria** per story (Given/When/Then).
6. **Estimate**: rough order of magnitude (1d / 3d / 1w / 2w+).
7. **Identify risks** that could blow up the estimate.

## Output template

```markdown
# <Product> — MVP PRD

## Problem
<one sentence>

## Target user
<persona, primary JTBD>

## In scope (MVP)
- <feature 1>
- <feature 2>

## Out of scope (v2)
- <thing intentionally cut>

## User stories
1. As …, I want …, so that … .
   - Given …, When …, Then … .

## Risks & open questions
- …

## Estimate
~<n> developer-weeks.
```

## Quality bar

- The MVP is shippable in ≤ 2 developer-weeks.
- Each story has at least one Given/When/Then.
- No technology choice in the PRD unless it is the user's hard
  constraint.

## Anti-patterns

- **Do not** include UI mockups unless asked — text first.
- **Do not** write stories you cannot verify ("user feels delighted").
- **Do not** scope-creep into "we should also build …". Keep that in
  the v2 list.
