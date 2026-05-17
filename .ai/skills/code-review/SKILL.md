---
id: code-review
name: Code review
description: |
  Review a diff or a set of files and surface bugs, smells, and missing
  test coverage. Optimised for short, actionable feedback rather than
  prose essays.
version: 0.1.0
tags: [review, lint, quality, refactor]
agents: ["*"]
triggers:
  - review
  - code review
  - lint
  - pr feedback
  - find bugs
  - code smell
  - refactor
inputModes: [text/plain]
outputModes: [text/plain, application/json]
mcp_servers: [fsbox]
examples:
  - "Review the changes in docker/agent-registry/server.py."
  - "Find bugs in the latest commit."
  - "What are the code smells in this file?"
securityRequirements: []
---

# Code review

## Purpose

Give the user a short, prioritised list of issues in a diff or file
set. Focus on bugs first, then security, then readability.

## When to use

- The user pastes a diff or asks "review …".
- A commit hash or PR number is referenced.
- The user asks for a "second pair of eyes".

## Inputs

- A diff (unified format), a commit range, or a list of file paths.
- Optionally, the language/framework conventions to apply.

## Procedure

1. **Read the diff in full.** Do not skim. Note the files touched.
2. **Build a mental model**: what is the change supposed to do? If it
   is unclear, ask the user before reviewing.
3. **Walk each hunk** and classify findings into:
   - **BUG** — concrete misbehaviour (off-by-one, null deref, race).
   - **SECURITY** — auth bypass, injection, secrets in logs, weak
     crypto, missing input validation.
   - **CORRECTNESS** — works today but fragile (missing edge case,
     undocumented invariant).
   - **STYLE** — naming, layout, comments. Lowest priority.
4. **Score** each finding `must-fix` / `should-fix` / `nit`.
5. **Suggest a fix** in 1-3 lines; do not rewrite the whole file.

## Output

A markdown list grouped by severity. Each entry:

```
- [must-fix · BUG] path/to/file.py:123 — `foo` is awaited twice; the
  second await raises `RuntimeError: cannot reuse already awaited
  coroutine`. Wrap in a Task or call once.
```

If the diff is clean, say so explicitly: "No must-fix or should-fix
findings; 2 nits below."

## Quality bar

- Every finding cites a path and line number.
- Each suggestion is implementable without further questions.
- No vague "consider refactoring" without a concrete target.

## Anti-patterns

- **Do not** rewrite the file. Suggest the smallest patch that fixes
  the issue.
- **Do not** lecture about general best practices unrelated to the
  diff.
- **Do not** rate the developer; rate the code.
- **Do not** invent imaginary requirements ("you should add metrics")
  unless the diff itself touches metrics.
