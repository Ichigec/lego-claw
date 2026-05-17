# Router — skill selection protocol

You are an agent loaded with a fixed set of skills under `.ai/skills/`.
Before answering, decide which skill (if any) is most relevant.

## 1. Decision procedure

1. Read the user's request verbatim.
2. For each skill, scan its frontmatter `triggers:` list.
   - If **any** trigger word appears (case-insensitive substring) in
     the request, mark the skill as a candidate.
3. If multiple candidates remain, prefer the one whose `tags:` overlap
   with the request's domain words.
4. If still tied, pick the skill with the highest `version:`.
5. If no skill matches, fall back to `AGENTS.md` defaults.

## 2. Loaded skills

The actual set is injected by `skills_loader.py`. Each entry below is a
short reminder. Defer to the skill's full `SKILL.md` body when working.

- **`a2a-build-agent`** — when the user says "add an agent",
  "A2A endpoint", "agent card", "task lifecycle", "JSON-RPC binding".
  Pull spec details via the OpenWebUI knowledge base `a2a-spec`.
- **`code-review`** — when the user says "review", "lint", "PR feedback",
  "find bugs", "code smell".
- **`webapp-testing`** — when the user says "e2e", "playwright",
  "browser test", "smoke test the UI".
- **`prd-mvp`** — when the user says "PRD", "MVP", "user story",
  "scope", "acceptance criteria".
- **`research`** — when the user says "research", "compare",
  "what's the state of the art", "survey".

## 3. Composition rules

- A skill's body is appended to the system prompt **after** AGENTS.md and
  this router. Do not duplicate instructions.
- A skill may declare extra MCP servers (`mcp_servers:` frontmatter).
  The skills loader merges those into the base `MCP_SERVERS` env when
  the agent process starts. Do not try to attach MCP servers at runtime.
- Skills are versioned. The Agent Card's `version` field is a sha256
  prefix of all loaded skill manifests; clients use it as an ETag.

## 4. When to refuse a skill

- The skill demands credentials the agent does not have (e.g. a real
  Playwright runner). Say so plainly and stop.
- The user's request crosses two skills with conflicting procedures.
  Surface the conflict to the user before guessing.
