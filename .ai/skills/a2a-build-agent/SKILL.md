---
id: a2a-build-agent
name: Build a new A2A-compliant agent
description: |
  End-to-end recipe for adding a new agent that speaks the A2A protocol
  (REST + JSON-RPC + gRPC), publishes an Agent Card on
  /.well-known/agent-card.json, and plugs into the existing agent-mesh
  stack (clawcode-adapter, openhands-adapter, agent-registry).
version: 0.1.0
tags: [a2a, agent, protocol, adapter, fastapi]
agents: [clawcode, openhands, opencode]
triggers:
  - a2a
  - agent card
  - add agent
  - new agent
  - task lifecycle
  - json-rpc binding
  - grpc binding
  - push notification
inputModes: [text/plain]
outputModes: [text/plain, application/json]
mcp_servers: [searchbox, fsbox]
examples:
  - "Add a new A2A agent called `linter-agent` backed by ruff."
  - "How do I add a Push Notification Config endpoint?"
  - "Wire a third agent into the agent-registry fan-out."
securityRequirements: []
---

# Build a new A2A-compliant agent

## Purpose

Use this skill when the user wants to add an agent that speaks the
[A2A protocol](https://a2a-protocol.org/). The repo already ships the
shared `agent-mesh-common` library and two reference adapters
(`clawcode-adapter`, `openhands-adapter`); this skill walks you through
adding a third one.

## When to use

- The user says "add a new agent" or "expose X as A2A".
- The request mentions a specific A2A artefact (Agent Card, Task,
  Artifact, Push Notification Config, Extended Agent Card).
- A teammate asks "how does our task lifecycle map to TASK_STATE_*?".

## Inputs you should ask for, if missing

- **Agent label and ID** (slug) — used in `AgentCard.name`/`AgentCard.id`.
- **Backing process** — CLI, HTTP service, or library call. Must be
  invokable headlessly (one call → one task).
- **Auth** — bearer token name. Defaults to `<AGENT>_ADAPTER_API_KEY`.
- **Skills** — which `.ai/skills/<id>/` should auto-attach. Defaults to
  `agents: ["*"]` for skills that omit a filter.

## Procedure

1. **Scaffold the adapter directory**

   ```
   docker/<agent>-adapter/
     Dockerfile         # mirror docker/clawcode-adapter/Dockerfile
     requirements.txt   # fastapi, uvicorn, pydantic, mcp, OTLP exporters
     server.py          # subclass `Runner`, return RunResult
   ```

2. **Implement the `Runner` protocol** in `server.py`:

   ```python
   from agent_mesh_adapter import (
       AdapterConfig, RunResult, Runner, build_app,
   )

   class MyRunner(Runner):
       agent_id = "myagent"
       agent_label = "My Agent"

       async def run_one_shot(self, *, task, workspace_subdir,
                              timeout_s, depth) -> RunResult: ...
   ```

   `build_app(runner, AdapterConfig(...))` mounts the legacy `/v1/run`,
   the A2A REST router on `/a2a/v1/*`, MCP SSE, and the bearer-gated
   `/openapi.json`.

3. **Register the agent** in:

   - `compose.agents-mesh.yml` — new service block with the same
     pattern (`network: llm-net`, `127.0.0.1:<port>:<port>`, OTLP env).
   - `.env` — add `<AGENT>_ADAPTER_API_KEY=$(openssl rand -hex 32)`.
   - `AGENT_REGISTRY_AGENTS` — append `<agent>-adapter:<port>` so the
     registry's fan-out includes the new card.

4. **Write at least one skill** under `.ai/skills/<your-skill>/SKILL.md`
   with `agents: [<your-agent-id>]` so the loader picks it up.

5. **Verify** with the Agent Card:

   ```
   curl -fsS http://<agent>-adapter:<port>/.well-known/agent-card.json \
     | jq '.skills[].id'
   ```

   The list should match the skills you declared in step 4. The
   `version` field changes whenever any SKILL.md changes.

## Spec lookup

When you need a precise quote from the A2A specification (Section X
numbers, exact field names), query the OpenWebUI knowledge base named
**`a2a-spec`**. It is chunked by section; ask for "Section 11.3
Agent-to-Agent REST" or "Section 4.4.5 AgentSkill schema" and you will
get the relevant fragment back.

## Output

Return a checklist of created files plus any compose / `.env` diffs.
For each touched file, include a short reason ("scaffolds Runner",
"registers in mesh"). End with a verification command the user can
copy-paste.

## Quality bar

- Every new endpoint is bearer-protected (or documented as health/probe
  exception).
- The Agent Card is JWS-signed; the public key is reachable at
  `/.well-known/jwks.json` on the same host.
- Skills file frontmatter validates against `AgentSkill` (Section 4.4.5):
  `id`, `name`, `description`, `tags`, `inputModes`, `outputModes`.

## Anti-patterns

- **Do not** invent a new auth scheme. Reuse `make_bearer_dep`.
- **Do not** mount the adapter on a public port. Loopback only —
  the cycle-guard + bearer + private network are the security model.
- **Do not** store Tasks in process memory in production; switch to
  `PostgresTaskStore` once you exit the prototype phase (P2 milestone).
- **Do not** edit `/v1/run` to add A2A behaviour — keep it as the
  deprecated alias and add new behaviour on `/a2a/v1/*` only.
