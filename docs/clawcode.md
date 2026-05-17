# Claw Code

CLI-агент [Claw Code](https://claw-code.codes/) — заявленный (март 2026) как
clean-room rewrite Claude Code. У проекта **нет web-GUI**, поэтому
интеграция в стек сделана как у `OpenHands`, но без `docker.sock` и без
nested-runtime: один контейнер, один интерактивный CLI, общий
`LiteLLM`-шлюз, общий sandbox.

> **⚠ Предупреждение о supply-chain.** Лендинг проекта сам сообщает о
> найденной supply-chain атаке на смежный пакет `axios`. Перед merge
> CLAWCODE_GIT_REF должен быть пин'нут на конкретный SHA, а lockfile —
> проаудитен. Контейнер запускается **без** `docker.sock` (в отличие от
> OpenHands, у Claw Code нет nested-runtime), под отдельным UID
> (10101) и не выставляет порт наружу.

Подготовленные артефакты:

- [`docker/clawcode/Dockerfile`](../docker/clawcode/Dockerfile) — мини-Dockerfile
  с `rust:1-slim` build-stage + `python:3.12-slim` runtime. Клонит
  репозиторий по `CLAWCODE_GIT_REF`, собирает Rust core (если есть
  `Cargo.toml`), ставит Python deps и заводит non-root user `claw` с
  uid `10101`.
- [`compose.clawcode.yml`](../compose.clawcode.yml) — отдельный compose в
  сети `llm-stack-net`. Bind-mount workspace и state, окружение для
  `OPENAI_API_BASE`/`OPENAI_API_KEY`/`LLM_MODEL`/`MCP_SERVERS`. Контейнер
  «спит» как PID 1 (`tail -f /dev/null`), чтобы интерактивная сессия
  открывалась через **`docker exec -it clawcode claw`** (Rust CLI из
  `rust/`; см. upstream `USAGE.md`). Файл `src/main.py` — только Python-harness
  (parity / аудит), **не** REPL: у него обязательные подкоманды вроде `turn-loop`.
- [`.env.clawcode`](../.env.clawcode) — тумблеры (image, git ref, default
  model, workspace, state, LiteLLM URL/key, UID/GID, MCP-конфиг). Политика
  общего дефолта с OpenWebUI/OpenHands — [docs/litellm-clients.md](litellm-clients.md).
- [`clawcode-start.sh`](../clawcode-start.sh) /
  [`clawcode-stop.sh`](../clawcode-stop.sh) — launcher и stopper с
  pre-flight'ами (Docker, сеть, LiteLLM, alias модели, ACL workspace).
- [`clawcode-demo-ru.sh`](../clawcode-demo-ru.sh) — headless smoke
  (зеркало `openhands-demo-ru.sh`).

## Запуск

```bash
bash stack-start.sh             # обычный стек
bash clawcode-start.sh          # build (если нужно) + start + интерактивный CLI
bash clawcode-start.sh --no-attach   # просто поднять контейнер
bash clawcode-demo-ru.sh        # readiness + headless chat-completions
bash clawcode-stop.sh           # остановить только Claw Code
```

`clawcode-start.sh` сам выставляет POSIX-ACL на
`$CLAWCODE_WORKSPACE_DIR` (`${HOME}/agent_dev` по умолчанию) под
uid `10101`, аналогично тому, как `openhands-start.sh` делает это для
uid `10001`.

## Связь с LiteLLM

В [`compose.clawcode.yml`](../compose.clawcode.yml) уже выставлены:

```yaml
OPENAI_API_BASE=http://litellm:4000/v1
OPENAI_BASE_URL=http://litellm:4000/v1
OPENAI_API_KEY=${LITELLM_API_KEY}
LLM_MODEL=openai/qwen3.6-35b-heretic
```

Все алиасы из [`docker/litellm/config.yaml`](../docker/litellm/config.yaml)
доступны через LiteLLM. Для **Rust `claw`** имя модели должно быть в
формате **`provider/model`** (например `openai/qwen3.6-35b-heretic`); в
`config.yaml` заведён отдельный `model_name` с этой строкой, параллельно
короткому `qwen3.6-35b-heretic` для OpenWebUI и smoke-скриптов.

## MCP

Через переменную `MCP_SERVERS` Claw Code получает JSON-список MCP
серверов. По умолчанию это — наш собственный `searchbox` (SSE-транспорт)
из [`compose.searchbox.yml`](../compose.searchbox.yml):

```yaml
MCP_SERVERS=[{"name":"searchbox","type":"sse","url":"http://searchbox:8090/sse"}]
```

Поскольку `clawcode` живёт в той же сети `llm-stack-net`, compose-DNS
`searchbox` ему виден напрямую — ровно как app-контейнеру `OpenHands`.
`docker.sock` не пробрасывается, поэтому проблема runtime-sandbox'ов
OpenHands (которым приходится ходить через `host.docker.internal:8024`)
к Claw Code не относится — у него нет nested-runtime.

Если хотите добавить ещё серверов — расширьте JSON в
`.env.clawcode → CLAWCODE_MCP_SERVERS`. Там же есть пример с
SearXNG/Wikipedia, см. [`mcp/README.md`](../mcp/README.md).

## Claw Code как tool через clawcode-adapter

Помимо ручного CLI, Claw Code публикуется агент-мешу через тонкий
адаптер [`docker/clawcode-adapter/`](../docker/clawcode-adapter/),
который поднимается из [`compose.agents-mesh.yml`](../compose.agents-mesh.yml).
Адаптер делает `docker exec clawcode claw --output-format json prompt "<task>"`
и возвращает ответ как FastAPI/MCP-SSE контракт. Подробности —
[`docs/agent-mesh.md`](agent-mesh.md).

Снаружи Claw виден как:

- **OpenAPI tool-server** в OpenWebUI (`http://clawcode-adapter:8790`);
- **MCP SSE сервер** для OpenHands (`http://host.docker.internal:8790/sse`
  через loopback host-port; обычные compose-сервисы — по
  `http://clawcode-adapter:8790/sse`);
- **LiteLLM-alias** `agent/clawcode` (Track 2 в плане).

⚠ **Cycle guard.** Claw НЕ получает собственный `clawcode-adapter` в
`CLAWCODE_MCP_SERVERS` — самоделегирование запрещено и в конфиге, и в
адаптере (`X-Agent-Mesh-Depth ≥ MAX_NESTED_AGENT_CALLS` → HTTP 429).
По дефолту Claw видит `searchbox` и `openhands-adapter`.

## OpenHands и Claw Code сосуществуют

Запуск Claw Code **никак не трогает** OpenHands и наоборот:

| | OpenHands | Claw Code |
| --- | --- | --- |
| compose-файл | `compose.openhands.yml` | `compose.clawcode.yml` |
| launcher | `openhands-start.sh` | `clawcode-start.sh` |
| образ | `openhands:1.6` (~2 GB) + runtime sandbox (~2-3 GB) | `voice-assistant-clawcode:local` (~0.5 GB) |
| docker.sock | да, нужен для nested runtime | **нет** |
| GUI | http://localhost:3300 | CLI only; в OpenWebUI делегируется через tool-server `clawcode-adapter` (`compose.agents-mesh.yml`) и алиас `agent/clawcode` в LiteLLM — см. [docs/agent-mesh.md](agent-mesh.md). |
| workspace | `${HOME}/agent_dev` (общий с fsbox / Jupyter) | то же самое |
| state | `~/.openhands` | `~/.clawcode` |
| RAM (idle) | ~0.6 GB | ~0.2 GB |
| VRAM | LLM не локально (через LiteLLM) | то же самое |

Можно держать оба контейнера запущенными — они просто пользуются одним
LiteLLM и одним sandbox-каталогом. Параллельные правки одного и того
же файла не разрешаются (как обычные конкурентные writes), поэтому
лучше разводить агентов по разным под-папкам или коммитам.

## Workspace и права доступа

Дефолтный `CLAWCODE_WORKSPACE_DIR` совпадает с OpenHands — это тот же
sandbox, который уже используют `fsbox` и host-side Jupyter. Внутри
контейнера он смонтирован в `/workspace/project` (как у OpenHands —
для совместимости логики поиска cwd / `.env` у общих библиотек).

Контейнер бежит под uid `10101`. На ext4 для этого работают POSIX-ACL,
которые `clawcode-start.sh` накатывает автоматически:

```bash
RUNTIME_UID=10101
sudo setfacl -R  -m u:$RUNTIME_UID:rwx,g:$RUNTIME_UID:rwx ${HOME}/agent_dev
sudo setfacl -R -dm u:$RUNTIME_UID:rwx,g:$RUNTIME_UID:rwx,u:1000:rwx,g:1000:rwx \
                 ${HOME}/agent_dev
```

## Что лежит вне репо

`.gitignore` исключает:

- `~/.clawcode/` — состояние Claw Code (история, MCP token cache);
- любые downloaded артефакты под `${HOME}/agent_dev` (это уже
  отдельный sandbox-репо).

Сам исходный код Claw Code в репо мы **не вендорим** — он клонится
внутри Dockerfile по `CLAWCODE_GIT_REF`. Это не `git submodule`, а
build-arg, чтобы было удобно фиксировать SHA. После
аудита поменяйте `CLAWCODE_GIT_REF=<commit-sha>` в `.env.clawcode`.

## Риски / aarch64

- **Образ собирается локально**, поэтому ARM64 (DGX Spark / GB10)
  работает «из коробки» — `python:3.12-slim` и `rust:1-slim` оба
  multi-arch.
- **Без docker.sock** — нет рисков privilege escalation через nested
  runtime, как у OpenHands.
- **Maturity** — проект новый. Любые pull'ы должны идти через pin'нутый
  SHA в `CLAWCODE_GIT_REF`, и `clawcode-start.sh` каждый раз делает
  `--build` чтобы было видно изменения.
- **LiteLLM master_key общий** — `LITELLM_API_KEY` из `.env`. Если
  ротейтите, перезапустите Claw Code и OpenHands.
