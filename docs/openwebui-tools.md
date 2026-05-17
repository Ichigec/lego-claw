# OpenWebUI tools: Web Search, Code Interpreter, CLI sandbox, Code-агент

This document describes the OpenWebUI tool integrations wired into our stack
on top of the existing chat / audio path:

- **Web Search** via a private SearXNG instance.
- **Code Interpreter / Code Execution** via a host-side Jupyter server, rooted
  at the code-agent sandbox `${HOME}/agent_dev`.
- **CLI tools (read-only)** via the original shellbox sidecar
  (mcpo + mcp-shell-server), still bound to `${HOME}/lego-claw:ro`.
- **Filesystem tools (read-write)** via the `fsbox` sidecar
  (mcpo + `@modelcontextprotocol/server-filesystem`), bound to
  `${HOME}/agent_dev:rw` — this is the «руки в чате» of the code-agent.
- **Optional CLI agents**: Aider (terminal) and Continue.dev (Cursor plugin),
  both pointed at the same LiteLLM model and the same sandbox.

All compose-side integrations share the `llm-stack-net` Docker network.

## Quick start

```bash
bash ./stack-start.sh   # brings up SearXNG, Jupyter, OpenWebUI, shellbox
bash ./stack-smoke.sh   # includes new sections A/B/C for the three tools
```

After OpenWebUI is up, visit `http://localhost:3000` and confirm:

- The chat input has a **+** button that exposes Web Search and Code Interpreter
  toggles.
- `Admin Panel → Settings → Tools` lists `shellbox` (pre-seeded on the first
  boot of a fresh `openwebui-data-volume`).

## 1. Web Search (SearXNG)

`compose.searxng.yml` runs:

- `searxng` (image `searxng/searxng:latest`) listening internally on
  `searxng:8080` and externally on `localhost:${SEARXNG_HOST_PORT:-8081}`.
- `searxng-redis` (Valkey, Redis-compatible) for SearXNG's rate-limit cache.

Configuration lives in [`docker/searxng/settings.yml`](../docker/searxng/settings.yml).
Key OpenWebUI-specific bits:

- `search.formats` includes `json` so OpenWebUI can parse the response.
- `server.limiter: false` so the JSON API does not 429 on bursts.
- `server.secret_key` is the placeholder `ultrasecretkey` — the upstream
  entrypoint replaces it with `$SEARXNG_SECRET` on every container start.

OpenWebUI reads:

| Variable | Default |
| - | - |
| `ENABLE_RAG_WEB_SEARCH` | `true` |
| `RAG_WEB_SEARCH_ENGINE` | `searxng` |
| `SEARXNG_QUERY_URL` | `http://searxng:8080/search?q=<query>&format=json` |
| `RAG_WEB_SEARCH_RESULT_COUNT` | `5` |
| `RAG_WEB_SEARCH_CONCURRENT_REQUESTS` | `10` |

Override them through `OPENWEBUI_*` keys in [`.env.openwebui`](../.env.openwebui).

**Verify** in chat: click `+` → Web Search → ask `погода в москве сегодня`.
Inline citations `[1]`/`[2]` should appear. From the host:

```bash
curl -s 'http://127.0.0.1:8081/search?q=test&format=json' | jq '.results | length'
```

> Other front-ends in this stack use different search engines: OpenHands
> ships with Tavily (cloud, off by default in our `.env.openhands`) plus a
> built-in headless browser, while opencode and Claw share our `searchbox`
> MCP. See [openhands.md → Поиск в вебе у разных решений](openhands.md#поиск-в-вебе-у-разных-решений)
> for the full comparison and how to wire each one up.

## 2. Code Interpreter (host-side Jupyter)

Architecture:

```
open-webui (container) ──host.docker.internal──▶ jupyter_server (host venv) ──▶ project FS
```

The Jupyter server runs **on the host**, not in Docker, because it needs full
read/write access to the project root. OpenWebUI reaches it through
`extra_hosts: host.docker.internal:host-gateway` (already wired in
`compose.openwebui.yml`).

### Bootstrap

The venv was created during install:

```bash
python3.12 -m venv .venv-jupyter
.venv-jupyter/bin/pip install jupyter_server ipykernel ipython notebook \
    nbformat matplotlib pandas numpy requests httpx
.venv-jupyter/bin/python -m ipykernel install --user --name=venv-jupyter \
    --display-name "Python (venv-jupyter)"
```

### Operating

```bash
./jupyter-host-start.sh start    # start (idempotent)
./jupyter-host-start.sh status   # current pid + /api liveness
./jupyter-host-start.sh logs 200 # tail the runtime log
./jupyter-host-start.sh stop
```

The server reads [`docker/jupyter/jupyter_server_config.py`](../docker/jupyter/jupyter_server_config.py),
which:

- binds to loopback (`127.0.0.1:8888`), accessible only from the host and
  containers using `host.docker.internal`;
- enforces token-only auth (`JUPYTER_TOKEN` from `.env.openwebui`);
- mounts the project root as `root_dir`;
- injects [`docker/jupyter/startup/00_helpers.py`](../docker/jupyter/startup/00_helpers.py)
  into every kernel via `IPKernelApp.exec_lines`.

### LLM-facing helpers

```python
ipython_history(n=20, search=None)  # last N IPython input cells
grep_source(pattern, path=PROJECT, glob=None)  # rg/grep over project tree
package_source(module)              # disk path of any importable module
```

Verify in chat:

- "Посчитай sin(1.5) и построй plt.plot" → returns inline base64 PNG.
- "Покажи последние 10 IPython команд" → triggers `ipython_history(10)`.
- "Найди все упоминания LMSTUDIO_API_BASE" → triggers `grep_source('LMSTUDIO_API_BASE')`.

### Security note

The kernel runs under your host user with full access to `~`, including `~/.ssh` and
`.env`-style credentials. Treat its blast-radius as identical to running
`python -i` in the project. If you need stricter isolation, switch to an
in-Docker jupyter that only mounts a subset of the tree read-only.

## 3. CLI sandbox (shellbox)

`compose.shellbox.yml` builds [`docker/shellbox/Dockerfile`](../docker/shellbox/Dockerfile),
which packs:

- `python:3.12-slim-bookworm` base.
- A small set of read-only utilities: `bash, coreutils, findutils, ripgrep, jq,
  curl, less, procps, file, gawk, sed, grep, tzdata`.
- `mcpo` (open-webui/mcpo, OpenAPI bridge) + `mcp-shell-server`
  (tumf/mcp-shell-server, MCP shell wrapper that enforces an `ALLOW_COMMANDS`
  whitelist).
- A non-root `runner` user (uid 10001).

The compose service hardens the container:

- `read_only: true`
- `cap_drop: [ALL]`
- `security_opt: ["no-new-privileges:true"]`
- `pids_limit: 64`, `mem_limit: 256m`, `cpus: 1.0`
- `tmpfs: [/tmp:size=64m,exec, /home/runner:size=8m,exec]`
- `volumes: ${HOME}/lego-claw:/workspace:ro` (read-only project mount)
- `ports: 127.0.0.1:8001:8001` (loopback only — OpenWebUI uses the
  `shellbox` DNS name on `llm-stack-net`)
- **No** `docker.sock`, **no** `privileged: true`.

`ALLOW_COMMANDS` whitelist:

```
ls, cat, head, tail, rg, grep, jq, find, stat, du, df, wc, awk, sed, file,
sort, uniq, echo, which, pwd, tr, cut, tee, less, date, env
```

### OpenWebUI registration

`stack-start.sh` pre-seeds the connection through the `TOOL_SERVER_CONNECTIONS`
PersistentConfig env var on first boot of a fresh `openwebui-data-volume`.

If your data volume already exists, register the tool through the API:

```bash
bash ./scripts/openwebui-register-shellbox.sh
```

The script reads `OPENWEBUI_VALIDATE_EMAIL/PASSWORD` (or
`OPENWEBUI_ADMIN_EMAIL/PASSWORD`) from `.env.openwebui`, signs in, and
POSTs `{"TOOL_SERVER_CONNECTIONS":[ ... shellbox entry ... ]}` to
`/api/v1/configs/tool_servers`. Re-runs are idempotent (it replaces any
prior entry whose `info.id == "shellbox"`).

Manual UI fallback:

1. Open `http://localhost:3000` → `Admin Panel → Settings → Tools`.
2. Add server `http://shellbox:8001`, type `OpenAPI`, auth `Bearer`,
   key = `$SHELLBOX_API_KEY`.
3. Per-user activation: in chat, click `+` → toggle `shellbox` ON.

### Verification

```bash
# OpenAPI listing
curl -s -H "Authorization: Bearer $SHELLBOX_API_KEY" \
    http://127.0.0.1:8001/openapi.json | jq '.paths | keys'

# Chat: "du -sh /workspace/data/*" → numeric output
# Chat: "rm /workspace/anything" → "Read-only file system"
# Chat: "cat /etc/shadow" → empty (file does not exist in the slim image,
#                                   and runner has no privileges anyway)
```

## 4. Code-агент

The «code-agent» layer turns the existing chat into a hands-on coding
assistant. The design and architectural trade-offs live in
[code-agent-options-comparison_e7737526.plan.md](../../.cursor/plans/code-agent-options-comparison_e7737526.plan.md);
this section documents the chosen rollout.

### Architecture

```
┌─ User browser ──────────┐    ┌─ Terminal CLI ────┐    ┌─ Cursor IDE ─────┐
│ open-webui :3000        │    │ aider             │    │ Continue.dev     │
└──────────┬──────────────┘    └─────────┬─────────┘    └─────────┬────────┘
           │                             │                        │
           ▼                             ▼                        ▼
       ┌────────────────────────────────────────────────────────┐
       │            litellm :4000  (sk-local)                   │
       │            qwen3.6-35b-heretic on host                 │
       └────────────────────────────────────────────────────────┘

OpenWebUI hands inside the chat:
  ├─ shellbox  → ${HOME}/lego-claw  (read-only, observe-only)
  ├─ fsbox     → ${HOME}/agent_dev     (read-write, typed FS API)
  └─ Jupyter   → ${HOME}/agent_dev     (kernel under host user uid)

CLI hands outside the chat:
  ├─ aider             → pwd-bound, run from ${HOME}/agent_dev
  └─ continue.dev      → workspace-bound (open agent_dev in Cursor)
```

### Sandbox: `${HOME}/agent_dev`

The agent has read-write access ONLY to this directory. It is created by
`stack-start.sh` (and by `jupyter-host-start.sh` on first run) with
`chmod 0750` and a fresh `git init`.

```bash
ls -ld ${HOME}/agent_dev
git -C ${HOME}/agent_dev log --oneline
```

To wipe the sandbox between tasks:

```bash
git -C ${HOME}/agent_dev clean -fdx
git -C ${HOME}/agent_dev reset --hard HEAD
```

The directory's own `README.md` documents the conventions; the rest of
[${HOME}/lego-claw](..) stays untouched.

### B3. fsbox — typed filesystem API for the chat

[`compose.fsbox.yml`](../compose.fsbox.yml) builds
[`docker/fsbox/Dockerfile`](../docker/fsbox/Dockerfile), which packs:

- `node:22-bookworm-slim` base.
- `mcpo` (OpenAPI bridge) installed via pip (PEP 668: `--break-system-packages`).
- `@modelcontextprotocol/server-filesystem` installed globally via npm — runs
  offline, no `npx` network fetch at startup.
- An `agent` user at uid `1000:1000` (matches the first non-root host user) so files written into
  `${HOME}/agent_dev` keep IDE-friendly ownership.

The compose service mirrors shellbox hardening:

- `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges]`.
- `pids_limit: 64`, `mem_limit: 256m`, `cpus: 1.0`.
- tmpfs for `/tmp` and `/home/agent`.
- `volumes: ${HOME}/agent_dev:/workspace:rw` — the **only** writable bind.
- `ports: 127.0.0.1:${FSBOX_HOST_PORT:-8002}:8001` (loopback only; OpenWebUI
  uses the `fsbox` DNS name on `llm-stack-net`).
- **No** `docker.sock`, **no** `privileged: true`.

Tools exposed by `mcp-server-filesystem` (typed, with MCP read/write hints):

```
read_text_file, read_media_file, read_multiple_files,
write_file, edit_file,
create_directory, list_directory, list_directory_with_sizes,
directory_tree, move_file, search_files, get_file_info,
list_allowed_directories
```

### OpenWebUI registration (fsbox)

`stack-start.sh` pre-seeds both shellbox and fsbox into
`TOOL_SERVER_CONNECTIONS` on first boot of a fresh `openwebui-data-volume`.
If the volume already exists, push them into the live config instead:

```bash
bash ./scripts/openwebui-register-shellbox.sh
bash ./scripts/openwebui-register-fsbox.sh
```

Both scripts read credentials from [`.env.openwebui`](../.env.openwebui), sign
in, and POST to `/api/v1/configs/tool_servers` (idempotent — they replace the
prior entry whose `info.id` matches).

Manual UI fallback: `Admin Panel → Settings → Tools` → add server
`http://fsbox:8001`, type `OpenAPI`, auth `Bearer`, key = `$FSBOX_API_KEY`.
Per-user activation: in chat, click `+` → toggle `fsbox` ON.

### Verification (fsbox)

```bash
# OpenAPI listing
curl -s -H "Authorization: Bearer $FSBOX_API_KEY" \
    http://127.0.0.1:8002/openapi.json | jq '.paths | keys'

# write_file → list_directory roundtrip via mcpo
curl -s -X POST -H "Authorization: Bearer $FSBOX_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"path":"/workspace/hello.txt","content":"hi from fsbox"}' \
    http://127.0.0.1:8002/write_file
ls -l ${HOME}/agent_dev/hello.txt   # owned by host user
```

In the chat: «создай файл `hello.py`, который печатает sin(1.5)» → fsbox's
`write_file` lands in `${HOME}/agent_dev/hello.py`; «теперь запусти его» →
the host-side Jupyter kernel (also rooted at `agent_dev`) executes it.

### Jupyter rebind (B1)

[`docker/jupyter/jupyter_server_config.py`](../docker/jupyter/jupyter_server_config.py)
now reads `JUPYTER_ROOT_DIR` from the environment (default
`${HOME}/agent_dev`). The previous default — the project root — was
unsafe because the kernel could overwrite anything under
`${HOME}/lego-claw`.

[`jupyter-host-start.sh`](../jupyter-host-start.sh):

- Refuses to run as root (so the kernel never writes uid-0-owned files into
  the sandbox; that was the «Running as root is not recommended» pain).
- Auto-creates `${HOME}/agent_dev` with `chmod 0750` if missing.
- Logs the active `JUPYTER_ROOT_DIR` in `start` and `status`.

To do a one-off data crunch against the main project:

```bash
JUPYTER_ROOT_DIR=${HOME}/lego-claw ./jupyter-host-start.sh restart
```

After the data run, restart without the override to fall back to the sandbox.

`grep_source` (via the IPython startup helpers) still defaults to
`${HOME}/lego-claw`, so the kernel can *read* the main project even
when its `root_dir` is the sandbox.

### A1. Aider — terminal CLI agent (optional)

Aider is installed via pipx as a regular user binary:

```bash
python3 -m pip install --user --break-system-packages pipx
~/.local/bin/pipx install aider-chat
```

Configuration lives in [`~/.aider.conf.yml`](${HOME}/.aider.conf.yml):

- `openai-api-base: http://localhost:4000/v1`
- `openai-api-key: sk-local`
- `model: openai/qwen3.6-35b-heretic`
- `auto-commits: false` — review every patch before committing.
- Chat / input / LLM histories live in `${HOME}/.aider/`, never in the
  project tree.

Always run aider from the sandbox:

```bash
cd ${HOME}/agent_dev
aider                       # opens a chat for the current git repo
aider some_file.py          # narrow the chat context to one file
```

Aider edits files in `$PWD` only, so running it from the main project would
be safe (read-only), but commits would noise up `cursor/first` — keep aider
constrained to `agent_dev`.

### A2. Continue.dev — Cursor / VSCode plugin (optional)

Continue.dev runs as a Cursor extension. Its global config lives in
[`~/.continue/config.json`](${HOME}/.continue/config.json):

- One model entry pointing at the LiteLLM endpoint (same key/base as aider).
- `roles: ["chat", "edit", "apply"]` — no autocomplete (no decent local
  autocomplete model on this stack).
- `systemMessage` reminds the model that
  `${HOME}/lego-claw` is read-only and edits belong in
  `${HOME}/agent_dev`.

To use: install the «Continue» extension in Cursor → open
`${HOME}/agent_dev` as the workspace → cmd-L for chat,
cmd-I for inline edit.

### Recommended default

Day-to-day:

- **B3 fsbox + B1 Jupyter** for chat-driven coding inside OpenWebUI.
- **A1 Aider** for «иди и доделай этот модуль, я приду через 10 минут».
- **A2 Continue.dev** when you already have the file open in Cursor and want
  a one-shot inline edit.
- **shellbox (read-only)** stays bound to the main project so the LLM can
  *research* it without being able to mutate it.

Skipped paths and why:

- **A3 OpenHands** — needs `docker.sock`, not worth the blast-radius for a
  35B-MoE model.
- **B2 shellbox+rw** — replaced by B3 fsbox, which has a typed API and does
  not need a command whitelist.

## 5. Multi-engine Search (searchbox)

`compose.searchbox.yml` поднимает собственный MCP-сервер, который
одновременно отдаётся как OpenAPI (через `mcpo`) на `:8001` и как
нативный MCP Streamable HTTP/SSE на `:8090`. Источники: локальный
SearXNG, Wikipedia/Wikidata, arXiv, GitHub, Hacker News, StackExchange,
Crossref, OpenAlex, PyPI, NPM, DuckDuckGo плюс опциональные
Brave/Google/Tavily. Исходники — [`mcp/`](../mcp/), подробный README —
[`mcp/README.md`](../mcp/README.md).

### Зачем нужен, если есть Web Search (SearXNG)

SearXNG-интеграция в разделе 1 — это **Web Search UI** (toggle «Web
Search» в чате, работает только через `RAG_WEB_SEARCH`). searchbox — это
**полноценный OpenAPI-тул**, который модель может вызывать как любой
другой function-call. Разница практическая:

| Возможность              | Web Search (SearXNG) | searchbox |
| ------------------------ | -------------------- | --------- |
| Вызов модели             | RAG-preprocessing    | Function call |
| Один запрос → N источников | только SearXNG    | SearXNG + 14 других |
| Отдельные движки         | нет                  | `search_<engine>` |
| Поддержка OpenHands      | только через URL-парсинг HTML | нативный MCP |
| Per-engine timeout       | —                    | есть |
| Явный fallback           | —                    | да (если SearXNG лёг, остальные работают) |

Короче: раздел 1 остаётся дефолтным «авто-поиском» OpenWebUI для
обычных чатов, а searchbox — для агентных сценариев, где модель сама
выбирает, куда идти (Wikipedia для дефиниций, arXiv для статей, GitHub
для репо).

### Регистрация

`stack-start.sh` пре-сидит searchbox вместе с shellbox+fsbox через
`TOOL_SERVER_CONNECTIONS` PersistentConfig при первом запуске свежего
`openwebui-data-volume`. На уже живом volume — форсируйте регистрацию:

```bash
bash ./scripts/openwebui-register-searchbox.sh
```

Скрипт идемпотентен, замещает запись `info.id=searchbox`.

Manual UI fallback: `Admin Panel → Settings → Tools` → add server
`http://searchbox:8001`, type `OpenAPI`, auth `Bearer`,
key = `$SEARCHBOX_API_KEY`. Per-user: `+` → toggle `searchbox` ON.

### Проверка

```bash
# OpenAPI listing (mcpo wrapper)
curl -s -H "Authorization: Bearer $SEARCHBOX_API_KEY" \
    http://127.0.0.1:8023/openapi.json | jq '.paths | keys'

# MCP status-tool (через OpenAPI)
curl -s -X POST -H "Authorization: Bearer $SEARCHBOX_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{}' \
    http://127.0.0.1:8023/search_status | jq

# Нативный MCP/SSE (используется OpenHands; без bearer)
curl -fsS http://127.0.0.1:8024/sse  # event-stream
```

В чате: «через tool `search` из searchbox найди первые 5 результатов
по “langchain rag tutorial”, покажи engines_used и per_engine». Модель
должна отдать merged список + разбивку по источникам.

### Дополнительные ключи

Опциональные API-ключи (в `.env` или `.env.openwebui`):

```bash
BRAVE_API_KEY=...       # включает Brave Search
GOOGLE_API_KEY=...      # вместе с GOOGLE_CX → Google Custom Search
GOOGLE_CX=...
TAVILY_API_KEY=...      # Tavily commercial search
GITHUB_TOKEN=...        # повышает GitHub rate-limit (10/min → 30/min)
STACKEXCHANGE_KEY=...   # 300/day → 10k/day
OPENALEX_MAILTO=you@... # polite-pool OpenAlex
```

Без ключей соответствующие движки помечаются `available: false` в
`list_engines`/`search_status` и не пытаются ходить в сеть.

## Smoke-test summary

`stack-smoke.sh` adds three new sections after the legacy LiteLLM/audio
checks:

- `== A. SearXNG ==`
- `== B. host-side Jupyter (Code Interpreter) ==`
- `== C. shellbox (mcpo + mcp-shell-server) ==`

Each section is independent — failures are warnings, not hard exits, so the
rest of the stack still validates.
