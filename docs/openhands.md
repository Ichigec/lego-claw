# OpenHands

Локальный GUI-агент [`All-Hands-AI/OpenHands`](https://github.com/All-Hands-AI/OpenHands)
поднимается в той же compose-сети, что и остальной стек, и ходит в LLM
исключительно через общий `LiteLLM`-шлюз. Никаких отдельных upstream'ов
прописывать не нужно: всё, что есть в [`docker/litellm/config.yaml`](../docker/litellm/config.yaml),
автоматически доступно OpenHands через префикс `openai/` в `LLM_MODEL` (см. ниже).

Подготовленные артефакты:

- `openhands/` — vendored клон репозитория на фиксированный тег `1.6.0`
  (корневой `lego-claw/` не git-репо для upstream'ов, поэтому реальный
  `git submodule add` невозможен).
- [`compose.openhands.yml`](../compose.openhands.yml) — отдельный compose в
  сети `llm-stack-net`, маппит UI на хост-порт `3300`, монтирует `docker.sock`,
  workspace и `~/.openhands` для состояния.
- [`.env.openhands`](../.env.openhands) — тумблеры (порт, образ, дефолтная
  модель, workspace, LiteLLM URL/key). Грузится поверх `.env`.
- [`openhands-start.sh`](../openhands-start.sh) / [`openhands-stop.sh`](../openhands-stop.sh)
  — launcher и stopper с pre-flight'ами (Docker, сеть, LiteLLM, workspace).

## Почему отдельный запуск

OpenHands — тяжёлая нагрузка, и держать его в `stack-start.sh` нет смысла:

- app-образ `docker.openhands.dev/openhands/openhands:1.6` ~2 ГБ;
- при первой сессии OpenHands динамически тянет runtime sandbox-образ
  (`ghcr.io/openhands/agent-server:1.15.0-python` — ещё ~2-3 ГБ);
- через `/var/run/docker.sock` он запускает вложенные контейнеры, поэтому
  ресурсы и привилегии выше, чем у обычных воркеров стека;
- на типичной работе со стеком (UI / voice / RAG) он не нужен.

Поэтому:

- запускается своим `openhands-start.sh`;
- зависит от уже поднятой LiteLLM (`stack-start.sh` остаётся первым шагом);
- остановка/перезапуск OpenHands не дёргает остальной стек.

## CLI vs Docker Compose

OpenHands можно ставить тремя способами:

| Способ                          | Когда                                              | Минусы                                                                                                                       |
| ------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `uv tool install openhands`     | Одноразовый эксперимент в собственном `~/.openhands` | Не в `llm-stack-net`, нужно вручную пробрасывать `host.docker.internal:4000`, нет `docker compose down` для всего сетапа.    |
| `openhands serve` (CLI wrapper) | То же, что выше, плюс auto-pull образов            | Тот же класс ограничений: shell-сессия = жизнь сервиса.                                                                      |
| **`docker compose` (наш путь)** | Стандарт для этого стека                           | Чуть больше yaml — но единая точка остановки, persistent volume, та же сеть, что у LiteLLM/Phoenix/OpenWebUI.                |

Поэтому стандарт здесь — `compose.openhands.yml`. CLI-варианты держим
в голове как fallback для одноразовых экспериментов.

## Связка с LiteLLM

В [`compose.openhands.yml`](../compose.openhands.yml) выставлены переменные,
которые читает OpenHands 1.6 при старте (фактический yaml в репо — источник правды):

```yaml
LLM_BASE_URL=http://host.docker.internal:4000/v1
LLM_API_KEY=${LITELLM_API_KEY:-sk-local}
LLM_MODEL=openai/${OPENHANDS_DEFAULT_MODEL:-qwen3.6-35b-heretic}
```

Почему **`host.docker.internal`**, а не `http://litellm:4000/v1`, и почему префикс **`openai/`**, а не `litellm_proxy/` — см. комментарии в том же compose-файле (кратко: runtime-песочницы на bridge не видят DNS `litellm`; префикс `litellm_proxy/` уходит на прокси как есть и даёт HTTP 400).

Сводная политика дефолтных имён и smoke-путь из «псевдо-sandbox» — в [docs/litellm-clients.md](litellm-clients.md).

В UI значения уже подставлены через env, но в `Settings → LLM → Advanced`
их можно подменить интерактивно — OpenHands перезапишет окружение в
`~/.openhands` и продолжит ходить новым адресом.

Все алиасы из [`docker/litellm/config.yaml`](../docker/litellm/config.yaml)
доступны как `openai/<model_name>` в соответствии с `LLM_MODEL` выше.

## Интеграция с OpenWebUI

OpenHands не выставляет OpenAI-совместимый endpoint, поэтому подружить
его с OpenWebUI «через API» невозможно. Интеграция идёт двумя путями:

1. **Общий LiteLLM-шлюз.** OpenWebUI и OpenHands смотрят в один
   `model_list` — это значит, что любая модель, которую вы видите в
   `OpenWebUI → Models`, также доступна в OpenHands → Settings → LLM.
2. **Кнопка-ссылка из OpenWebUI на OpenHands UI.**
   - В [`.env.openwebui`](../.env.openwebui) есть `OPENWEBUI_BANNERS` — JSON
     со встроенным баннером, который ведёт на `http://localhost:3300`. Он
     попадает в env OpenWebUI как `WEBUI_BANNERS` и подхватывается при
     первом запуске на чистом `openwebui-data-volume`. После первого
     запуска значения хранятся в БД OpenWebUI; чтобы изменить баннер на
     уже инициализированной инсталляции, правьте его в
     `Admin Panel → Settings → Banners`.
   - Альтернатива без баннера — добавить ссылку через
     `Admin Panel → Settings → Interface → Pinned Tools` (UI-only,
     persistent в БД OpenWebUI).

То есть «связка» = (а) единый LLM gateway + (б) явная навигация UI→UI.

## OpenHands как tool через openhands-adapter

Помимо ручного GUI и CLI, OpenHands публикуется в agent-mesh через
тонкий адаптер [`docker/openhands-adapter/`](../docker/openhands-adapter/),
который поднимается из [`compose.agents-mesh.yml`](../compose.agents-mesh.yml).
Адаптер запускает `openhands --headless --json -t "<task>"` (свой
ephemeral runtime-sandbox под каждый вызов) и возвращает ответ как
FastAPI/MCP-SSE контракт. Полная схема — [`docs/agent-mesh.md`](agent-mesh.md).

Снаружи OpenHands виден как:

- **OpenAPI tool-server** в OpenWebUI (`http://openhands-adapter:8791`);
- **MCP SSE сервер** для Claw Code (`http://openhands-adapter:8791/sse`
  по compose-DNS);
- **LiteLLM-alias** `agent/openhands` (Track 2 в плане).

⚠ **Cycle guard.** OpenHands НЕ получает собственный `openhands-adapter`
в `OPENHANDS_MCP_SERVERS` — самоделегирование запрещено и в конфиге, и в
адаптере (`X-Agent-Mesh-Depth ≥ MAX_NESTED_AGENT_CALLS` → HTTP 429). По
дефолту OpenHands видит `searchbox` и `clawcode-adapter` (через
`host.docker.internal:<loopback>`, потому что runtime-sandbox не
резолвит compose-DNS).

## Поиск в вебе у разных решений

В этой сборке у нас живут несколько фронтов (OpenWebUI, OpenHands, opencode),
и каждый ходит за веб-поиском по-своему. Сводка — какой поисковик у кого
включён, что задано в репо и что нужно сделать, чтобы переключить.

| Решение      | Поисковик                                  | Где включается                                                                  | Состояние в этом репо                                                                                  |
| ------------ | ------------------------------------------ | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| OpenWebUI    | self-hosted **SearXNG** (наш контейнер)    | `RAG_WEB_SEARCH_ENGINE=searxng`, `SEARXNG_QUERY_URL=…` в [compose.openwebui.yml](../compose.openwebui.yml) | **Включён по умолчанию** — `compose.searxng.yml` поднимается из `stack-start.sh`.                       |
| OpenHands    | **Tavily** (`https://api.tavily.com`) через MCP `tavily-mcp@0.2.1`, плюс встроенный **Playwright-браузер** для произвольных URL | `TAVILY_API_KEY` (или `SEARCH_API_KEY`) в env контейнера; см. [openhands/openhands/core/config/mcp_config.py](../openhands/openhands/core/config/mcp_config.py) | **Tavily выключен** — ключ нигде не задан; есть только встроенный браузер.                              |
| opencode / Claw | МСР-tool `searchbox` (наш контейнер, 15 движков) | `mcp/openhands_mcp.json` / `mcp/clawcode_mcp.json` — cross-wired через `compose.agents-mesh.yml` | **Включён по умолчанию**, рядом с SearXNG.                                                              |

### OpenWebUI → SearXNG (локально, без внешних API)

`OpenWebUI` ходит за поиском в наш собственный SearXNG-контейнер по DNS
`http://searxng:8080` внутри `llm-stack-net`. Никакие внешние API-ключи
не нужны — SearXNG агрегирует результаты сам.

Включённые upstream-движки в [docker/searxng/settings.yml](../docker/searxng/settings.yml):
`google`, `bing`, `wikipedia`, `wikidata`, `github`, `stackoverflow`, `arxiv`.
DuckDuckGo выключен из-за его агрессивных rate-limit'ов, ломающих UX.

Подробности и проверка чата — в [openwebui-tools.md → 1. Web Search (SearXNG)](openwebui-tools.md#1-web-search-searxng).

### OpenHands → Tavily (по умолчанию выключен) + headless-браузер

`OpenHands` сам по себе **не ходит** в наш SearXNG: его «штатный»
веб-поиск — это MCP-сервер `tavily-mcp@0.2.1` (npx-пакет), который
вызывает облачное API `https://api.tavily.com/search`. Регистрируется
сервер в [openhands/openhands/core/config/mcp_config.py](../openhands/openhands/core/config/mcp_config.py)
методом `OpenHandsMCPConfig.add_search_engine`, и активируется только
если в env контейнера выставлен `TAVILY_API_KEY` (или его alias
`SEARCH_API_KEY` — `app_server/config.py` читает оба).

В нашем `compose.openhands.yml` ни `TAVILY_API_KEY`, ни `SEARCH_API_KEY`
не пробрасываются, и `.env.openhands` их не задаёт. Это сознательно:
весь стек спроектирован как локально-замкнутый, без обязательных
SaaS-ключей.

Без Tavily у агента остаётся **встроенный Playwright-браузер**
(`browse`/`browse_url`-инструменты в `openhands/runtime/browser/`),
которым он сам открывает произвольные URL — в том числе HTML-страницу
нашего же SearXNG. То есть в простейшем виде поиск работает так:

> Скажите OpenHands: «Открой `http://host.docker.internal:8081/search?q=<запрос>`
> и собери первые 5 ссылок» — он сходит туда браузером и распарсит HTML
> (`http://searxng:8080` тоже подходит, но `host.docker.internal` стабильнее
> работает из вложенных runtime-сэндбоксов, где compose-DNS не виден —
> это та же причина, по которой LiteLLM мы зовём через `host.docker.internal`).

Это «ручной» вариант: OpenHands не знает про наш SearXNG как про
структурированный search-engine, он просто видит сайт.

#### Если хочется реального Tavily в OpenHands

1. Получите ключ на [tavily.com](https://tavily.com/) (бесплатный тариф
   ≈ 1k запросов/мес).
2. Добавьте проброс ключа в `compose.openhands.yml` в блок `environment:`:

   ```yaml
   - TAVILY_API_KEY=${TAVILY_API_KEY:-}
   ```

3. Положите сам ключ в `.env` (а не в `.env.openhands` — `openhands-start.sh`
   фильтрует загрузку по allowlist `_OH_ENV_KEYS`, и `TAVILY_API_KEY`
   там нет; `docker compose --env-file .env` всё равно подставит его в
   YAML на этапе compose-substitution).
4. `bash openhands-stop.sh && bash openhands-start.sh` — после рестарта
   `add_search_engine` зарегистрирует MCP-сервер `tavily`, и в Settings
   → MCP появится новый сервер; `browse`-инструмент остаётся доступен
   как и раньше.

Альтернатива без облака — собственный MCP-сервер-обёртка над нашим
SearXNG, прописанный в Settings → MCP → Add Server. В этом стеке он уже
собран и поднимается из `compose.searchbox.yml` — см. раздел ниже.

### OpenHands → локальный MCP-Search (searchbox)

В стеке запущен собственный MCP-сервер [`compose.searchbox.yml`](../compose.searchbox.yml),
который на одном порту отдаёт нативный MCP (Streamable HTTP `/mcp` +
SSE `/sse`) и на соседнем — OpenAPI-обёртку через `mcpo`. Источники:
SearXNG (локальный), Wikipedia/Wikidata, arXiv, GitHub, Hacker News,
StackExchange, Crossref, OpenAlex, PyPI, NPM, DuckDuckGo + опциональные
Brave/Google/Tavily по ключам в `.env`.

Подключение в UI:

1. Открыть `http://localhost:3300` → `Settings → MCP → Add Server`.
2. Тип транспорта — `sse` (поддержка Streamable HTTP в OpenHands 1.6
   доступна, но SSE стабильнее в runtime-sandbox'ах).
3. URL — **зависит от того, кто ходит**:

   | Кто ходит в MCP                   | URL                                                 |
   | --------------------------------- | --------------------------------------------------- |
   | app-контейнер (`openhands`)       | `http://searchbox:8090/sse` (llm-stack-net DNS)     |
   | runtime-sandbox (дочерний docker) | `http://host.docker.internal:8024/sse`              |

   Runtime-sandbox'ы OpenHands спавнятся через `docker.sock` и уходят в
   default bridge-сеть, поэтому compose-DNS `searchbox` им не виден —
   используем hostport `8024` (значение `SEARCHBOX_MCP_HOST_PORT` из
   [`.env.openwebui`](../.env.openwebui)) через `host.docker.internal`.
   Это тот же трюк, что мы используем для LiteLLM.

4. Аутентификация — не требуется: нативный MCP HTTP/SSE на `:8090`
   отдаётся без bearer-токена (OpenAPI-порт `:8001` с `mcpo` защищён
   `SEARCHBOX_API_KEY`, и его использует только OpenWebUI).

JSON-snippet для ручной вставки — [`mcp/openhands_mcp.json`](../mcp/openhands_mcp.json).

Альтернатива через env (если вы пинитесь версии OpenHands, которая
читает `MCP_SERVERS` как JSON):

```yaml
# compose.openhands.yml → services.openhands.environment
- MCP_SERVERS=[{"name":"searchbox","type":"sse","url":"http://searchbox:8090/sse"}]
```

Проверка в чате: «найди через `search` первые 5 результатов по
“LangChain RAG tutorial”, сразу в нескольких движках». Агент должен
увидеть tool `search` в списке доступных MCP-tools и отдать merged
результат с полями `results[].source` = `searxng`/`duckduckgo`/…

## Запуск

```bash
bash stack-start.sh        # обычный стек: Phoenix / LiteLLM / OpenWebUI / ...
bash openhands-start.sh    # OpenHands отдельно, тяжёлый
# UI: http://localhost:3300
bash openhands-stop.sh     # остановить только OpenHands
```

### Демонстрация для пользователя

После того как стек и (при необходимости) OpenHands уже подняты, быстрый
русскоязычный проход с проверкой Docker, сети `llm-stack-net`, LiteLLM,
доступности UI и одним headless-запросом `chat/completions` через тот же
алиас, что использует OpenHands:

```bash
bash ./openhands-demo-ru.sh
```

Если контейнер OpenHands ещё не запущен, можно за один вызов поднять его
после pre-flight'ов стека:

```bash
bash ./openhands-demo-ru.sh --start
```

Аналог по основному стеку (OpenWebUI, аудио, Phoenix): [`stack-demo-ru.sh`](../stack-demo-ru.sh).

Pre-flight'ы внутри `openhands-start.sh`:

1. `docker info` — пользователь видит docker daemon из текущей сессии.
2. `docker network inspect llm-stack-net` — сеть существует
   (создаётся `stack-start.sh`).
3. `curl -H "Authorization: Bearer $LITELLM_API_KEY" http://localhost:4000/v1/models`
   — LiteLLM отвечает и принимает ключ.
4. В ответе `/v1/models` есть `OPENHANDS_DEFAULT_MODEL` — иначе предупреждение.
5. Workspace и state-каталоги создаются (`mkdir -p`).
6. После `docker compose up -d` ждём `GET /` на хост-порте до таймаута 180s.

## Workspace

### Архитектура: app vs runtime sandbox

OpenHands 1.x — **два** контейнера, а не один:

1. **app** (`openhands` из нашего compose) — FastAPI/UI, БД сессий.
   Сам никаких tool-use не выполняет.
2. **runtime sandbox** (`oh-agent-server-<id>`, образ
   `ghcr.io/openhands/agent-server:1.15.0-python`) — спавнится
   **динамически через `docker.sock`** под каждую conversation. Именно в нём
   агент крутит `terminal`, `file_editor`, `browser` и т.д.

Любой volume mount, прописанный для app-контейнера, **не наследуется**
runtime-сандбоксом. По умолчанию runtime работает в эфемерном docker volume
`openhands-workspace-<sandbox_id>`, который удаляется вместе с conversation
из UI. Это означает: код, который агент создаёт в `/workspace/...`, на хост
**сам по себе не попадёт**.

### Как пробросить workspace в runtime — `SANDBOX_VOLUMES`

Env, которую читает app-контейнер и пробрасывает дальше в спавнящиеся
runtime'ы (см. `openhands/app_server/config.py` — парсинг `SANDBOX_VOLUMES`
→ `VolumeMount` → `DockerSandboxServiceInjector(mounts=…)`):

```yaml
SANDBOX_VOLUMES=/host/path:/container/path:rw[,/host2:/container2:ro,...]
```

В нашем `compose.openhands.yml`:

```yaml
- SANDBOX_VOLUMES=${OPENHANDS_WORKSPACE_DIR:-${HOME}/agent_dev}:/workspace/project:rw
```

После этого **новые** conversation'ы получают `${HOME}/agent_dev`
смонтированным внутрь runtime-контейнера как `/workspace/project`, и всё,
что агент туда пишет, моментально появляется на хосте.

> ⚠ Старые conversation'ы, созданные до правки, продолжают жить в своих
> старых docker volume'ах — рестарт app их не трогает. Чтобы перевести
> на новый mount, удалите старые сессии из UI (правый клик → Delete) и
> начните новый чат, либо пересоздайте контейнеры:
>
> ```bash
> bash openhands-stop.sh && bash openhands-start.sh
> # затем New Conversation в UI
> ```

#### Почему именно `/workspace/project`, а не `/workspace`

Контейнер runtime'а стартует с `--workdir /workspace/project` (значение
захардкожено в `openhands/app_server/sandbox/docker_sandbox_spec_service.py:50`,
env-override нет). Сам агент-сервер — PyInstaller-frozen бинарник; при
импорте `litellm` он зовёт `dotenv.find_dotenv()`, который для frozen
бинарей использует `os.getcwd()`. Если `/workspace/project` не существует
в момент старта — `os.getcwd()` падает с `OSError: Starting path not
found`, runtime валится с exit code 1 ещё до того, как успевает поднять
HTTP-сервер, а UI показывает «Sandbox failed to start within 120s».

Симптом в логах:

```text
File "litellm/__init__.py", line 115, in <module>
File "dotenv/main.py", line 354, in load_dotenv
File "dotenv/main.py", line 263, in _walk_to_root
OSError: Starting path not found
```

Mount именно в `/workspace/project` решает проблему: Docker создаёт
точку монтирования вместе с родительским `/workspace`, агент-сервер
получает существующий cwd, а две вспомогательные папки runtime'а
(`OH_CONVERSATIONS_PATH=/workspace/conversations` и
`OH_BASH_EVENTS_DIR=/workspace/bash_events`) остаются внутри эфемерной
image-FS — это runtime-метаданные, дублировать их на хост не нужно
(durable-история чатов всё равно лежит в `~/.openhands/openhands.db`).

#### Права доступа на host-папке (POSIX ACL)

Внутри runtime-образа агент-сервер крутится не как root и не как ваш
host-пользователь, а как встроенный юзер `openhands` с **uid `10001`**
(в `agent-server:1.15.0-python` — проверено `docker exec ... id`). Если
`${HOME}/agent_dev` сохранила дефолтные права `750 <host-user>:<host-user>`,
runtime попадёт в категорию «others» (`---`) и упадёт уже на старте —
`pydantic_settings` / `dotenv` пытаются прочитать `.env` в cwd и
получают `PermissionError: [Errno 13]`:

```text
File "fastmcp/__init__.py", line 14, in <module>
File "pydantic_settings/sources/providers/dotenv.py", line 100, in _read_env_files
PermissionError: [Errno 13] Permission denied: '.env'
```

Чистое решение — POSIX ACL: сохраняем владельца `<host-user>:<host-user>` (важно
для git/IDE/fsbox), но добавляем `rwx` для uid'а runtime'а и
наследование на новые файлы. На ext4 это «из коробки»:

```bash
RUNTIME_UID=10001  # фиксировано для agent-server:1.15.0-python
RUNTIME_GID=10001
sudo setfacl -R  -m u:$RUNTIME_UID:rwx,g:$RUNTIME_GID:rwx ${HOME}/agent_dev
sudo setfacl -R -dm u:$RUNTIME_UID:rwx,g:$RUNTIME_GID:rwx,u:1000:rwx,g:1000:rwx \
                 ${HOME}/agent_dev
```

`openhands-start.sh` накатывает эти ACL автоматически при каждом запуске
(см. шаг «Workspace ACL» в скрипте). Если хотите проверить вручную —
после правки рестарт OpenHands не нужен, ACL применяются ядром мгновенно;
достаточно завести **новый** чат в UI. UID можно уточнить на любом живом
runtime'е:

```bash
sudo docker exec $(sudo docker ps --filter name=oh-agent-server -q | head -1) id
```

### Почему `${HOME}/agent_dev`

`OPENHANDS_WORKSPACE_DIR` по умолчанию = `${HOME}/agent_dev` —
тот же sandbox, который уже используют `fsbox` и host-side Jupyter.
Благодаря `SANDBOX_VOLUMES` это даёт нам реальную shared-FS между
агентами:

- OpenHands редактирует те же файлы, что и Code Interpreter в OpenWebUI;
- `git status` в любом из этих окружений видит коммиты другого
  (`${HOME}/agent_dev` — git-репо, см. его `README.md`);
- состояние и история OpenHands лежат в `${OPENHANDS_STATE_DIR:-~/.openhands}`
  (вне репо).

Если эта общая папка нежелательна (например, надо изолировать OpenHands
от code-агента OpenWebUI) — задайте свой путь в `.env.openhands`:

```bash
OPENHANDS_WORKSPACE_DIR=${HOME}/openhands_only
```

### Где смотреть результаты работы агента

| Что | Где |
|---|---|
| Файлы, которые правит агент | `${HOME}/agent_dev/...` (в UI / shell внутри runtime — `/workspace/project/...`) |
| Diff по сессии | `git -C ${HOME}/agent_dev status` / `git diff` |
| События conversation'а (tool-use лог) | `~/.openhands/v1_conversations/<id>/*.json` (читать как host user, владелец — `root`, права 755) |
| Live-поток событий | `docker logs -f openhands` (включено `LOG_ALL_EVENTS=true`) |
| История чатов / settings | `~/.openhands/openhands.db` (SQLite), `~/.openhands/settings.json` |

## Риски / aarch64 (GB10 / DGX Spark)

- App-образ `docker.openhands.dev/openhands/openhands:1.6` —
  multi-arch, но runtime sandbox-образ исторически собирался под
  `linux/amd64`. См. [issue #9257](https://github.com/All-Hands-AI/OpenHands/issues/9257).
  Если при первой сессии OpenHands ругается «no matching manifest for
  linux/arm64» при пуле runtime-образа — используйте более свежий тег
  `OPENHANDS_AGENT_SERVER_TAG` или соберите образ локально через
  [`containers/sandbox/Dockerfile`](https://github.com/All-Hands-AI/OpenHands/tree/main/containers/sandbox)
  и пропишите свой репозиторий в `OPENHANDS_AGENT_SERVER_REPO`.
- Версия app vs runtime: связка `1.6 ↔ 1.15.0-python` — официальный
  quickstart. Расхождение версий даёт «exec format error» внутри
  sandbox (см. [issue #3525](https://github.com/All-Hands-AI/OpenHands/issues/3525)).
- `docker.sock` пробрасывается внутрь контейнера. Это даёт OpenHands
  права запускать любой контейнер, включая привилегированный — это
  фундаментальное требование v1-runtime, обходить его не пытаемся, но
  держим OpenHands только в локальной dev-инсталляции и не выставляем
  3300-порт наружу.
- LiteLLM master_key в OpenHands и OpenWebUI **общий**: `LITELLM_API_KEY`
  из `.env`. Если ротейтите ключ — перезапустите оба контейнера.

## Что лежит вне репо

`.gitignore` исключает:

- `openhands/` — vendored сорцы (≈сотни МБ);
- `data/openhands/` — артефакты, если когда-нибудь начнём писать туда;
- `~/.openhands/` живёт в HOME пользователя, не в репо.

Не коммитим vendored сорцы и состояние OpenHands.
