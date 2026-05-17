# Skills (`.ai/skills/`) — общий каталог для агентов

Формат — superset Claude Code «skills» и [aitmpl.com](https://aitmpl.com)
шаблонов. Каждый скилл — папка `.ai/skills/<id>/` с обязательным
`SKILL.md` (YAML-frontmatter + body) и опциональными `mcp.json`,
`assets/`. Этот каталог разделяется между `clawcode`, `openhands`,
`opencode` и любым новым A2A-агентом через bind-mount.

## Зачем

- **Единый источник правды** для всех агентов: правит человек один раз,
  все агенты подхватывают.
- **AgentCard skills** (Section 4.4.5) генерируются из frontmatter — не
  нужно дублировать описание в адаптере и в карточке.
- **System prompt** агента собирается автоматически: `AGENTS.md` (global
  rules) + `router.md` (skill selection) + frontmatter+body всех скиллов
  с `agents:` совпадающим по `agent_id`.
- **MCP** — `mcp_servers:` в скилле автоматически попадает в env
  `MCP_SERVERS` headless-процесса.
- **AgentCard.version bump**: sha256 от concat'а manifest'ов; меняется →
  клиенты делают conditional GET (ETag, Section 8.6).

## Layout

```
.ai/
  AGENTS.md                 # always-on rules
  router.md                 # skill selection protocol
  skills/
    a2a-build-agent/
      SKILL.md
      mcp.json              # optional extra MCP servers
      assets/               # optional templates
    code-review/
    webapp-testing/
    research/
    prd-mvp/
```

## SKILL.md frontmatter

Подмножество, маппящееся в `AgentSkill` (Section 4.4.5), отмечено `→`.

```yaml
---
id: webapp-testing                # → AgentSkill.id (slug, [a-z0-9-])
name: Web App Testing             # → AgentSkill.name
description: Playwright e2e tests # → AgentSkill.description
version: 0.2.0                    # bump → AgentCard.version пересчитается
tags: [testing, playwright, e2e]  # → AgentSkill.tags
agents: [clawcode, openhands, opencode]  # * | список agent_id; кто авто-маунтит
triggers: [test, e2e, playwright] # для router.md matching
inputModes: [text/plain]          # → AgentSkill.inputModes
outputModes: [text/plain, application/json]  # → AgentSkill.outputModes
mcp_servers: [searchbox]          # дополнительные MCP, нужные скиллу
examples:
  - "Run e2e tests on the local app"
securityRequirements: []          # optional, → AgentSkill.securityRequirements
---

# Purpose
…

# When to use
…

# Inputs
…

# Procedure
…

# Output
…

# Quality bar
…

# Anti-patterns
…
```

## Жизненный цикл

| Действие | Кто делает | Эффект |
|---|---|---|
| Положить `SKILL.md` в репо | человек | следующий `stack-start.sh` / `/admin/reload` пересоберёт AgentCard. |
| Создать через UI | `skills-manager` (`POST /skills`) | пишет в `.ai/skills/<id>/SKILL.md` + POST `/admin/reload` каждому адаптеру. |
| Импортировать `aitmpl` | `skills-manager` (`POST /skills/import`) | дергает `claude-code-templates --skill=...` и сохраняет результат. |
| Toggle для агента | `skills-manager` (`POST /skills/{id}/attach`) | правит `agents: [...]` идемпотентно. |
| Bump версии | bump `version:` в frontmatter | sha256 от manifest'ов меняется → `AgentCard.version` тоже. |

## Bootstrap-набор скиллов

Поднимается из коробки в `.ai/skills/`:

- **`a2a-build-agent`** — как добавить нового A2A-агента в стек.
  Ссылается на RAG-knowledge `a2a-spec` (см. [`docs/a2a-rag.md`](a2a-rag.md))
  для прицельных цитат спеки.
- **`code-review`** — diff-aware review с проверкой на anti-patterns.
- **`webapp-testing`** — Playwright e2e через MCP `webapp-testing`.
- **`prd-mvp`** — генерация PRD/MVP-плана из идеи.
- **`research`** — web search + чтение источников через `searchbox`.
- **`opencode-workflow`** — opencode-специфичный flow (LSP-диагностика
  через `find.symbols`, ACP-сессии, cycle-guard). `agents: [opencode]`.

## Mount-карта (компоуз)

| Контейнер | Путь внутри | Mode | Назначение |
|---|---|---|---|
| `clawcode-adapter` | `/workspace/project/.ai` | `:ro` | загрузка карточек + сборка system prompt |
| `openhands-adapter` | `/workspace/project/.ai` | `:ro` | то же |
| `opencode-adapter` | `/workspace/project/.ai` | `:ro` | то же (плюс инжектится в ACP `session/new` как system prompt) |
| `clawcode` (REPL) | `/workspace/project/.ai` | `:ro` | live-сессия видит те же скиллы |
| `openhands` (UI) | `/workspace/project/.ai` | `:ro` | то же |
| `opencode` (TUI / Web / ACP) | `/workspace/project/.ai` | `:ro` | opencode также имеет нативный `AGENTS.md` reader |
| `skills-manager` | `/data/skills` | `:rw` | CRUD на тех же файлах |

`SKILLS_DIR` / `CLAWCODE_SKILLS_DIR` / `OPENHANDS_SKILLS_DIR` /
`OPENCODE_SKILLS_DIR` env-vars в `.env` (и в `.env.opencode` для
последнего) указывают на хост-путь (по умолчанию `./.ai`).

> **Особый случай opencode:** у opencode есть нативный `AGENTS.md`
> reader. Чтобы избежать дупликации, `opencode-adapter` инжектит в
> `session/new.system` **только matched-skills** (без `AGENTS.md`+`router.md`).
> `AGENTS.md` остаётся за opencode-native механизмом. См.
> [`docs/opencode.md`](opencode.md).

## Router (`router.md`)

`router.md` — короткий контракт, как агент выбирает скилл по запросу:
матчит `triggers` (case-insensitive substrings), пересечение `agents:`
с собственным `agent_id`. При неоднозначности — спросить пользователя.
`AGENTS.md` всегда добавляется первым; `router.md` — вторым; затем
конкатенируются matched skills.

См. [`docs/agent-mesh.md`](agent-mesh.md) — общая архитектура,
[`docs/a2a-rag.md`](a2a-rag.md) — RAG-knowledge `a2a-spec`,
[`docs/a2a-walkthrough.md`](a2a-walkthrough.md) — live-сценарии.
