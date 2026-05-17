# A2A walk-through: сценарии живой проверки agent-mesh

Этот документ — **пошаговая инструкция** для живой проверки в браузере и
терминале того, что код-агенты (Claw Code, OpenHands, **opencode**)
реально:

1. видны OpenWebUI как обычные tool-серверы (Track 1, «agent as tool»);
2. видны LiteLLM как обычные «модели» `agent/clawcode`, `agent/openhands`
   и `agent/opencode` (Track 2, A2A Agent Gateway);
3. видят друг друга как MCP-tool через адаптеры;
4. **никогда** не видят сами себя (cycle-guard).

После четырёх сценариев — расширенный раздел про **трассировку**: где именно
видно, что код-агент сделал каждый шаг.

Базовая архитектура — [`docs/agent-mesh.md`](agent-mesh.md). Smoke-скрипт —
[`agent-mesh-demo-ru.sh`](../agent-mesh-demo-ru.sh).

## Перед стартом

```bash
# .env содержит все три токена адаптеров (если нет — сгенерируйте openssl rand -hex 32)
grep -E '^(CLAWCODE|OPENHANDS|OPENCODE)_ADAPTER_API_KEY=' .env

bash stack-start.sh                                    # phoenix + litellm + openwebui + adapters
bash agent-mesh-demo-ru.sh                             # readiness без живых вызовов
bash agent-mesh-demo-ru.sh --check-mcp                 # cross-wiring MCP

# Опционально (если ещё не подняты как UI)
bash clawcode-start.sh --no-attach                     # Claw REPL
bash openhands-start.sh                                # OpenHands UI на :3300
bash opencode-start.sh --no-attach                     # opencode (TUI + ACP bridge)
bash opencode-web-start.sh                             # опц. opencode web на :3400
```

Дальше предполагаем, что:

- OpenWebUI открыт на `http://localhost:3000` (модель по умолчанию
  `qwen3.6-35b-heretic`, интерфейс на русском);
- адаптеры зарегистрированы как tool-серверы (если data-volume старый —
  выполнить `bash scripts/openwebui-register-agent-mesh.sh`);
- LiteLLM публикует `agent/clawcode`, `agent/openhands` и `agent/opencode`
  в `/v1/models` (если БД свежая — `bash scripts/litellm-register-agent-mesh.sh`).

## Сценарий 1 — OpenWebUI как «диспетчер» (Track 1, tool-server)

**Цель:** пользователь общается с обычной чат-моделью; модель сама решает
делегировать задачу `clawcode-adapter` или `openhands-adapter` через
function-call.

1. Откройте `http://localhost:3000`, войдите admin'ом.
2. Создайте новый чат, оставьте модель `qwen3.6-35b-heretic`.
3. В поле ввода нажмите «➕» (Tools) и включите оба сервера:
   `clawcode-adapter` и `openhands-adapter`. (Иконка должна стать активной.)
4. Отправьте промпт по-русски, явно намекающий на делегацию:

   > Используя tool clawcode-adapter (`run` или `POST /v1/run`),
   > попроси Claw Code кратко описать .py-файлы в `/workspace/project`
   > и вернуть список одной фразой по-русски.

5. В ответе модели должна появиться карточка вызова tool'а
   (OpenWebUI рисует её над финальным ответом). В ней видно: имя сервера
   (`clawcode-adapter`), endpoint (`POST /v1/run`), payload (`task`,
   `timeout_s`) и сырой ответ адаптера (`result_text`, `files_changed`,
   `duration_s`, `depth`).
6. После tool-call'а модель вставляет резюме `result_text` в обычный текст
   ответа.

**Что проверять:**

- В Phoenix появилась цепочка из 2-х span'ов: LiteLLM `chat.completion`
  (модель `qwen3.6-35b-heretic`) → adapter span `agent_mesh.run.clawcode`.
- `docker logs -f clawcode-adapter` напечатает `clawcode run depth=1
  workdir=/workspace/project task_len=… cmd=docker exec -i -w …`.

## Сценарий 2 — OpenWebUI как прямой A2A-клиент (Track 2)

**Цель:** показать, что любой OpenAI-совместимый клиент может звать агента
**как модель**: `model: agent/clawcode`. Никаких tool-servers, всё идёт через
LiteLLM-алиас, который проксирует прямо в адаптер.

1. В том же или новом чате OpenWebUI: смените модель на `agent/clawcode`
   (или `agent/openhands`) — оба должны появиться в селекторе моделей.
2. Отправьте обычный промпт, например:

   > Кратко по-русски опиши, что ты сейчас видишь в `/workspace/project`,
   > и закончи словом ГОТОВО.

3. Ответ придёт **без** карточки tool-call'а — для OpenWebUI это просто
   chat-completion. На самом деле LiteLLM приняла запрос, проксировала его
   на `http://clawcode-adapter:8790/v1/chat/completions`, адаптер
   развернул `messages → task` и сделал `POST /v1/run`.

То же самое из терминала:

```bash
curl -fsS -X POST http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer $LITELLM_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
          "model":"agent/clawcode",
          "messages":[{"role":"user","content":"опиши /workspace/project и закончи ГОТОВО"}]
        }' | jq .
```

**Что проверять:**

- В Phoenix span `chat.completion` имеет `model = agent/clawcode`,
  внутри — дочерний `agent_mesh.run.clawcode` (благодаря единому
  `PHOENIX_PROJECT_NAME=qwen3.6-heretic`).
- `docker logs litellm` показывает запрос к
  `http://clawcode-adapter:8790/v1/...`.

## Сценарий 3 — Claw зовёт OpenHands через MCP

**Цель:** агент сам становится клиентом другого агента. Claw видит
`openhands-adapter` как обычный MCP-сервер (`run_openhands` — это название
tool'а внутри MCP, а не endpoint URL).

1. В терминале откройте Claw REPL: `bash clawcode-start.sh` (если
   контейнер уже работает — он подцепит существующую сессию).
2. В REPL отправьте:

   ```text
   Используя MCP-tool из сервера openhands-adapter (имя инструмента
   run_openhands), попроси OpenHands создать
   /workspace/project/from-claw.txt со строкой
   «привет от Claw через OpenHands». Подожди завершения и скажи,
   какой статус вернул tool.
   ```

3. Claw должен вызвать tool'у `run_openhands`. В выводе REPL вы увидите
   JSONL-событие `tool_use` (имя `run_openhands`, аргументы `task`,
   `timeout_s`) и затем `message` от OpenHands с текстом ответа.
4. Проверьте: `cat ${HOME}/agent_dev/from-claw.txt` (это путь, на который
   замаплен `/workspace/project` через `OPENHANDS_WORKSPACE_DIR` в
   `.env.openhands`).

**Cycle-guard:** Claw НЕ видит `run_clawcode` — `clawcode-adapter` явно
исключён из `CLAWCODE_MCP_SERVERS` в `.env.clawcode`. Если попросить «вызови
самого себя» — Claw честно ответит, что такой tool ему не виден.

## Сценарий 4 — OpenHands зовёт Claw через MCP

Зеркальный сценарий.

1. Откройте OpenHands UI: `http://localhost:3300` (если не поднят:
   `bash openhands-start.sh`). Создайте новый чат.
2. Отправьте по-русски:

   ```text
   Через MCP-tool сервера clawcode-adapter (имя инструмента run_clawcode)
   попроси Claw Code создать /workspace/project/from-oh.txt со строкой
   «привет от OpenHands через Claw». В ответе укажи поле depth, которое
   вернул tool, — это поможет проверить cycle-guard.
   ```

3. В UI отображается каждый шаг: agent reasoning → call `run_clawcode` →
   payload → tool result. Когда задача завершится, файл появится на хосте:
   `cat ${HOME}/agent_dev/from-oh.txt`.

**Cycle-guard:** OpenHands НЕ видит `run_openhands` (см.
`OPENHANDS_MCP_SERVERS` в `.env.openhands`). Если намеренно попросить
«делегируй задачу OpenHands-у» — ответ будет «такого MCP-tool у меня нет».
Адаптер дополнительно вернул бы `HTTP 429` благодаря заголовку
`X-Agent-Mesh-Depth`.

## Что вообще делают код-агенты (видимость пошагово)

Каждый агент — это отдельный процесс с собственным циклом «модель ↔ tool ↔
файловая система». Адаптер заворачивает этот цикл в HTTP, но внутри ничего
не прячет: вы можете наблюдать каждый шаг.

| Слой | Что делает | Где видно |
|---|---|---|
| LLM-клиент (OpenWebUI / Claw / OpenHands UI) | Решает, дернуть ли tool / какую модель выбрать. | Сам UI; OpenWebUI рисует tool-call карточку. |
| LiteLLM | Принимает chat.completion, маршрутизирует в адаптер (Track 2) либо просто транзитит chat (Track 1). | `docker logs litellm`; Phoenix span `chat.completion`. |
| Адаптер (FastAPI) | Авторизует bearer, считает `X-Agent-Mesh-Depth`, спавнит CLI агента (`docker exec` для Claw, `docker run` runtime-sandbox для OpenHands), парсит JSONL-вывод. | `docker logs -f clawcode-adapter` / `openhands-adapter`; Phoenix span `agent_mesh.run.<id>`. |
| CLI агента | «Агентский цикл»: think → tool-call → observe → think → ответ. Стримит события построчно как JSONL. | `docker logs -f clawcode` (Claw long-lived) / временный sandbox-контейнер OpenHands; `result_text` + `files_changed` в HTTP-ответе. |
| Файловая система | Реальные файлы под `${HOME}/agent_dev` (хост) ↔ `/workspace/project` (внутри контейнеров). | `ls -la ${HOME}/agent_dev`. |

Конкретно по агентам:

- **Claw Code** — это Rust-CLI `claw --output-format json prompt "<task>"`.
  Каждая строка stdout — JSON-событие: `tool_use` (имя/аргументы tool'а),
  `message` (текст модели), `assistant`, `done`. Адаптер собирает финальные
  `message`/`assistant` в `result_text`. Команда, которую он выполняет, видна
  в логах: `clawcode run depth=1 workdir=… task_len=… cmd=docker exec -i -w …`.
- **OpenHands** — `openhands --headless --json -t "<task>"`. На каждый вызов
  адаптер спавнит свежий runtime-sandbox-контейнер (`ghcr.io/openhands/agent-server`),
  агент работает там и контейнер удаляется. JSONL-события: `{"type":"action",
  "action":"write","args":{"path":"…","content":"…"}}`, `{"action":"read",…}`,
  `{"source":"agent","message":"…"}`. Адаптер парсит их в
  `files_changed` + `result_text`.

## Трассировка / наблюдаемость

Семь слоёв трассировки расположены в порядке «от пользователя к диску».
Все они работают **одновременно** — берите тот, что ближе к точке вопроса.

### 1. Phoenix UI — главный путь (рекомендуется первым)

`http://localhost:6006`, проект **`qwen3.6-heretic`** (один на весь стек,
переопределяется `PHOENIX_PROJECT_NAME`).

Что туда пишут:

- **LiteLLM** (`success_callback: arize_phoenix` в
  `docker/litellm/config.yaml`): каждый chat.completion / embedding /
  audio вызов любого клиента, включая Track 2 (`model=agent/clawcode`).
- **Адаптеры** через OTLP/HTTP (`OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006/v1/traces`):
  span'ы `agent_mesh.run.clawcode`, `agent_mesh.run.openhands`,
  `agent_mesh.sessions.create.<id>`, `agent_mesh.sessions.message.<id>`.
- Атрибуты span'ов: `agent.id`, `agent.depth`, `task.length`, `run.ok`,
  `run.exit_code`, `session.id`, `session.status`, `message.length`.

В Track 2 LiteLLM-span и adapter-span попадают в одну трейс-цепочку
автоматически (общий traceparent). В Track 1 они тоже видны рядом, но как
отдельные корневые span'ы (LLM-клиент → LiteLLM, и параллельно
LLM-клиент → adapter; объединить можно по timestamp + agent.id).

### 2. HTTP-заголовок `X-Agent-Mesh-Depth`

Каждый ответ адаптера содержит этот заголовок (echo-middleware) — там лежит
`incoming + 1`. Самый дешёвый способ убедиться, что cycle-guard видит ваш
запрос:

```bash
curl -i -X POST http://127.0.0.1:8790/v1/run \
    -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
    -H "X-Agent-Mesh-Depth: 0" \
    -H "Content-Type: application/json" \
    -d '{"task":"echo","timeout_s":10}' \
    | head -n 20
# < X-Agent-Mesh-Depth: 1
```

При `depth ≥ MAX_NESTED_AGENT_CALLS` тот же запрос вернёт `HTTP 429` с
JSON-`detail`, объясняющим, что лимит достигнут.

### 3. `docker logs` адаптеров (структурированные info-логи)

```bash
docker logs -f clawcode-adapter
# 2026-05-11 ... INFO clawcode-adapter: clawcode run depth=1 workdir=/workspace/project
#                     task_len=42 cmd=docker exec -i -w /workspace/project -e

docker logs -f openhands-adapter
# 2026-05-11 ... INFO openhands-adapter: openhands run depth=1 workdir=/workspace/project
#                     task_len=42 budget=900s
```

Поля: `depth`, `workdir`, `task_len`, для OpenHands ещё `budget`. Если
адаптер вернул не-OK — ниже будут `stderr_tail` и `exit_code`.

### 4. `docker logs` самих агентов (что делал CLI)

```bash
docker logs -f clawcode                              # long-lived REPL/headless
docker logs -f $(docker ps --filter ancestor=ghcr.io/openhands/agent-server \
                 --format '{{.Names}}' | head -n1)    # текущий sandbox
```

Здесь видно полный JSONL-стрим агента (`tool_use`, `message`, `action`,
`observation`, `error`). Это то же самое, что адаптер парсит в
`result_text`/`files_changed`.

### 5. `docker logs litellm`

Полезно, когда подозреваете, что Track 2 не достучался до адаптера.

```bash
docker logs -f litellm | grep -E 'agent/(clawcode|openhands)'
```

Здесь видно: какой клиент позвал, на какой upstream LiteLLM пошёл
(`http://clawcode-adapter:8790/...`) и что вернул.

### 6. OpenWebUI tool-call inspector

В чате OpenWebUI каждый tool-call раскрывается в карточку с полями
`endpoint`, `request body`, `response`. Полезно для Сценария 1, когда LLM
сам выбирает между двумя адаптерами.

### 7. OpenHands UI шаги (`http://localhost:3300`)

OpenHands UI пошагово отрисовывает события агента в правой панели:
ReadAction, WriteAction, BrowseInteractiveAction, MessageAction… Это то же
самое, что headless-OpenHands пишет в JSONL stdout — только в визуальной
форме. Полезно для Сценария 4.

### Быстрая мини-памятка

| Вопрос | Куда смотреть |
|---|---|
| Кто и когда вообще дернул адаптер? | Phoenix → проект `qwen3.6-heretic` → spans `agent_mesh.run.*`. |
| Что именно делал агент шаг за шагом? | `docker logs -f clawcode` или контейнер OpenHands sandbox; либо OpenHands UI шаги. |
| Cycle-guard сработал? | Заголовок `X-Agent-Mesh-Depth` в ответе и/или `HTTP 429`. |
| LiteLLM не нашла модель `agent/*`? | `docker logs litellm`; `bash scripts/litellm-register-agent-mesh.sh`. |
| OpenWebUI не показывает tool? | `bash scripts/openwebui-register-agent-mesh.sh`; в чате `➕ → toggle …`. |
| Файл реально создан? | `ls -la ${HOME}/agent_dev` (= `/workspace/project` внутри). |

## Что проверить «по чек-листу» после демо

- [ ] Сценарий 1: Phoenix показал `chat.completion` + `agent_mesh.run.clawcode`.
- [ ] Сценарий 2: Phoenix показал `chat.completion` модели `agent/clawcode`,
      внутри — `agent_mesh.run.clawcode`.
- [ ] Сценарий 3: `${HOME}/agent_dev/from-claw.txt` существует;
      Claw в логах писал `tool_use` для `run_openhands`.
- [ ] Сценарий 4: `${HOME}/agent_dev/from-oh.txt` существует;
      OpenHands UI показал `WriteAction` от Claw.
- [ ] `bash agent-mesh-demo-ru.sh` — все шаги зелёные.
- [ ] `bash agent-mesh-demo-ru.sh --check-mcp` — никто не видит сам себя.

См. также:

- [`docs/agent-mesh.md`](agent-mesh.md) — архитектура и конфиг.
- [`docs/litellm-clients.md`](litellm-clients.md) — как ещё клиенты могут
  звать LiteLLM (включая Track 2).
- [`docs/openhands.md`](openhands.md), [`docs/clawcode.md`](clawcode.md) —
  специфика двух code-агентов отдельно.
- [`stack-smoke.sh`](../stack-smoke.sh) (раздел `D. agent-mesh adapters`) —
  smoke-проверка адаптеров в составе общего скрипта.

## Сценарий 5 — A2A AgentCard discovery + JWS verification

**Цель:** убедиться, что адаптеры публикуют spec-compliant AgentCard,
правильно подписывают его JWS (Section 8.4) и что
`agent-registry` верифицирует подписи на лету.

1. Прочитайте «лицевую» (publicly-visible, без auth) карту:

   ```bash
   curl -fsS http://127.0.0.1:8790/.well-known/agent-card.json | jq .
   curl -fsS http://127.0.0.1:8791/.well-known/agent-card.json | jq .
   ```

   В ответе должны присутствовать `protocolVersion`, `name`, `version`,
   `interfaces` (3 binding'а: REST, JSON-RPC, gRPC),
   `securitySchemes`, `defaultInputModes`, `defaultOutputModes`,
   `capabilities.streaming = true`,
   `capabilities.pushNotifications = true`,
   `skills[]` (наполняется skills loader'ом), и в самом конце поле
   `signatures: [{ protected, signature }]` — это JWS detached
   signature по canonicalized JSON (RFC 8785).

2. Прочитайте extended card (auth-gated, Section 5.8):

   ```bash
   curl -fsS -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
       http://127.0.0.1:8790/a2a/v1/extendedAgentCard | jq .
   ```

   Может содержать приватные скиллы / расширенные capabilities, не
   видимые в обычной карте.

3. Соберите discovery через `agent-registry`:

   ```bash
   curl -fsS -H "Authorization: Bearer $AGENT_REGISTRY_API_KEY" \
       http://127.0.0.1:8794/v1/agents | jq '.[] | {name, version, interfaces}'
   ```

   Registry опрашивает оба адаптера, кеширует ETag (Section 8.6.1) и
   проверяет JWS перед публикацией. Если подпись не сошлась —
   запись помечается `"signatureValid": false` и не отдаётся клиентам
   как достоверная.

4. Проверьте подпись руками (опционально):

   ```bash
   jq '.signatures[0]' < <(curl -fsS http://127.0.0.1:8790/.well-known/agent-card.json)
   # → { "protected": "<b64u>", "signature": "<b64u>" }
   curl -fsS http://127.0.0.1:8790/.well-known/jwks.json | jq .
   ```

   Достаточно прогнать `cryptography` + `jcs` + базовые JOSE-биты —
   код для это лежит в `docker/agent-mesh-common/jws.py`.

## Сценарий 6 — Cross-agent ListTasks через agent-registry

**Цель:** показать, что у OpenWebUI есть единый «инбокс задач»: один URL,
данные сразу из всех адаптеров (Section 3.1.4 — pagination).

1. Создайте по одной задаче на каждом адаптере, обе с одним `contextId`:

   ```bash
   CTX="ctx-$(openssl rand -hex 6)"
   curl -fsS -X POST http://127.0.0.1:8790/a2a/v1/message:send \
     -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
     -H "Content-Type: application/json" \
     -d "{\"message\":{\"role\":\"ROLE_USER\",\"contextId\":\"$CTX\",\"parts\":[{\"kind\":\"text\",\"text\":\"ls /workspace/project\"}]},\"returnImmediately\":true}"

   curl -fsS -X POST http://127.0.0.1:8791/a2a/v1/message:send \
     -H "Authorization: Bearer $OPENHANDS_ADAPTER_API_KEY" \
     -H "Content-Type: application/json" \
     -d "{\"message\":{\"role\":\"ROLE_USER\",\"contextId\":\"$CTX\",\"parts\":[{\"kind\":\"text\",\"text\":\"echo hi\"}]},\"returnImmediately\":true}"
   ```

2. Fan-out через registry:

   ```bash
   curl -fsS -H "Authorization: Bearer $AGENT_REGISTRY_API_KEY" \
       "http://127.0.0.1:8794/v1/tasks?agents=*&contextId=$CTX&pageSize=20" | jq .
   ```

   В ответе будет `tasks[]` из обоих адаптеров с префиксом
   `<agentId>:<taskId>`. `nextPageToken` — base64(ts+id) для cursor-based
   continuation (Section 3.1.4).

3. Отменить любую задачу через registry:

   ```bash
   curl -fsS -X POST \
     -H "Authorization: Bearer $AGENT_REGISTRY_API_KEY" \
     "http://127.0.0.1:8794/v1/tasks/clawcode-adapter:$TID:cancel"
   ```

## Сценарий 7 — Push notifications

**Цель:** убедиться, что `PushDispatcher` доставляет webhook'и с
правильным `X-A2A-Notification-Token` и делает retry/backoff на 5xx.

1. Поднимите тестовый webhook на хосте (можно `python -m http.server`
   или `ncat -lk -p 9000 -c 'printf "HTTP/1.1 200 OK\r\n\r\n"'`).
2. Зарегистрируйте config:

   ```bash
   TID=$(curl -fsS -X POST http://127.0.0.1:8790/a2a/v1/message:send \
     -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"message":{"role":"ROLE_USER","parts":[{"kind":"text","text":"echo hello"}]},"returnImmediately":true}' \
     | jq -r '.id')

   curl -fsS -X POST "http://127.0.0.1:8790/a2a/v1/tasks/$TID/pushNotificationConfigs" \
     -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"url":"http://host.docker.internal:9000/webhook","token":"my-secret-token"}'
   ```

3. На приёмнике должны прилетать POST'ы с заголовком
   `X-A2A-Notification-Token: my-secret-token` и телом
   `{taskId, status, finalEvent?, artifactUpdate?}`. После terminal
   state (`COMPLETED`/`FAILED`/`CANCELED`) — финальный POST и
   автоматическая остановка polling-loop'а.
4. Симулируйте 503 (`ncat`-сценарий с `printf "HTTP/1.1 503 ...`) —
   в логах адаптера будет видно `push retry attempt=N delay=Ms`
   (экспоненциальный backoff `A2A_PUSH_BACKOFF_BASE_S * 2^N`,
   ограниченный `A2A_PUSH_MAX_RETRIES`).

## Сценарий 8 — In-task auth (TASK_STATE_AUTH_REQUIRED)

**Цель:** показать end-to-end Section 7.6: runner попросил creds,
Task ушла в `TASK_STATE_AUTH_REQUIRED`, клиент догнал через
`tasks/get`, дал токен, runner продолжил.

1. Стрельните задачей, которая внутри runner'а вызовет
   `request_auth(...)` (для smoke-проверки нужен skill, который
   сознательно требует токен — пример в
   `.ai/skills/research/SKILL.md`, секция «secrets»). Получите
   `taskId`.
2. Подпишитесь стримом:

   ```bash
   curl -N -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
     "http://127.0.0.1:8790/a2a/v1/tasks/$TID:subscribe"
   # … увидите событие
   #   data: {"statusUpdate":{"state":"TASK_STATE_AUTH_REQUIRED",
   #       "message":{"metadata":{"authChallenge":{"schemes":["bearer"],"challengeId":"<cid>"}}}}}
   ```

3. Прочитайте challenge на отдельной ручке (можно если SSE-сессия
   уже умерла):

   ```bash
   curl -fsS -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
       "http://127.0.0.1:8790/tasks/$TID/auth:challenge" | jq .
   ```

4. Передайте creds:

   ```bash
   curl -fsS -X POST \
     -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
     -H "Content-Type: application/json" \
     "http://127.0.0.1:8790/tasks/$TID/auth:provide" \
     -d '{"scheme":"bearer","value":"<token>","challengeId":"<cid>"}'
   ```

   Task возвращается в `TASK_STATE_WORKING`, runner внутри получает
   `AuthCredentials(scheme="bearer", value="<token>")` и продолжает.

5. Если challenge так и не пришёл (например, OpenWebUI потерял
   сессию), повтор:

   ```bash
   curl -fsS -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
       "http://127.0.0.1:8790/a2a/v1/tasks/$TID" | jq '.status'
   ```

   — pending challenge виден в `status.message.metadata.authChallenge`.

## Сценарий 9 — opencode (ACP) ↔ OpenWebUI / MCP

> Зеркало Сценария 1, но через opencode (`opencode-adapter` → ACP-bridge
> через `docker exec -i opencode opencode acp`). Подробности модели
> взаимодействия — [`docs/opencode.md`](opencode.md).

**Цель:** убедиться, что:

1. opencode виден OpenWebUI как обычный tool-server **и** как
   LiteLLM-модель `agent/opencode` (Track 2);
2. ACP-bridge внутри `opencode-adapter` корректно обрабатывает
   `session/new` + `session/prompt` + входящие
   `fs/*` / `terminal/*` / `session/request_permission` от opencode;
3. opencode видит `clawcode-adapter` / `openhands-adapter` как MCP
   (cross-wiring), но НЕ видит сам себя (cycle-guard).

### A. Readiness и ACP smoke

```bash
# Все readiness-чеки + ACP initialize + session/new напрямую через docker exec.
bash opencode-demo-ru.sh
# То же + один POST /v1/run через opencode-adapter.
bash opencode-demo-ru.sh --adapter
# Расширенный smoke в составе общего скрипта:
bash agent-mesh-demo-ru.sh --run --opencode
```

Что должно быть зелёным:

- `docker inspect opencode` → `running` (либо `--start` сам поднимет).
- `docker exec opencode opencode acp` отвечает на NDJSON `initialize`
  с полем `protocolVersion`.
- `curl http://127.0.0.1:8798/healthz` → `{"status":"ok"}`.
- LiteLLM `/v1/models` содержит `agent/opencode`.
- Все три cross-MCP подняты, opencode НЕ видит `opencode-adapter`.

### B. OpenWebUI как диспетчер (Track 1)

1. `http://localhost:3000` → новый чат → модель `qwen3.6-35b-heretic`.
2. «➕» → toggle `opencode-adapter`.
3. Промпт:

   > Используя tool `opencode-adapter` (`POST /v1/run`), попроси
   > opencode прочитать `.py`-файлы в `/workspace/project` и кратко
   > описать одной фразой по-русски. Закончи словом ГОТОВО.

4. В карточке tool-call появится: endpoint, payload (`task`,
   `timeout_s`), и ответ адаптера. В Phoenix — span
   `agent_mesh.run.opencode` плюс LiteLLM `chat.completion`.

### C. OpenWebUI как прямой A2A-клиент (Track 2)

1. В селекторе моделей выберите `agent/opencode`.
2. Отправьте обычный промпт; LiteLLM проксирует на
   `http://opencode-adapter:8798/v1/chat/completions`, адаптер
   разворачивает messages → task → ACP `session/new` + `session/prompt`.

```bash
curl -fsS -X POST http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer $LITELLM_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
          "model":"agent/opencode",
          "messages":[{"role":"user","content":"опиши /workspace/project и закончи ГОТОВО"}]
        }' | jq .
```

### D. Claw / OpenHands зовут opencode через MCP

В `.env.clawcode → CLAWCODE_MCP_SERVERS` и
`.env.openhands → OPENHANDS_MCP_SERVERS` присутствует
`opencode-adapter`. Из Claw REPL или OpenHands UI:

```text
Используя MCP-tool сервера opencode-adapter (имя инструмента run_opencode),
попроси opencode создать /workspace/project/from-opencode.txt со строкой
«привет от opencode через ACP». В ответе укажи stopReason и duration_s.
```

После завершения — `cat ${HOME}/agent_dev/from-opencode.txt`.

### E. Cycle-guard

```bash
# opencode-adapter сам не пускает себя дальше depth=1:
curl -i -X POST http://127.0.0.1:8798/v1/run \
    -H "Authorization: Bearer $OPENCODE_ADAPTER_API_KEY" \
    -H "X-Agent-Mesh-Depth: 1" \
    -H "Content-Type: application/json" \
    -d '{"task":"test","timeout_s":10}'
# → HTTP 429, X-Agent-Mesh-Depth: 2.
```

`OPENCODE_MCP_SERVERS` явно не содержит `opencode-adapter` — opencode
изнутри ACP-сессии тоже не увидит себя в списке MCP-tools.

### F. Permissions policy

`OPENCODE_ADAPTER_AUTO_APPROVE=workspace` (default) одобряет файловые
операции только внутри `/workspace/project`. Тест:

```text
[в OpenWebUI чате с opencode-adapter tool'ом]
Попроси opencode прочитать /etc/hostname. Что ты получил в ответе?
```

opencode пошлёт `fs/read_text_file`, адаптер откажет
(`outside workspace`), opencode сообщит ошибку в `result_text`. Если
поставить `OPENCODE_ADAPTER_AUTO_APPROVE=all` (только для trusted-демо),
тот же запрос пройдёт.

**Что проверять:**

- Phoenix span `agent_mesh.run.opencode` с атрибутами `agent.id=opencode`,
  `agent.depth=1`, `run.ok=true`.
- `docker logs -f opencode-adapter` — структурированные строки
  `opencode run depth=1 workdir=…` плюс `ACP session=<id>` логи.
- `docker logs opencode` (idle TUI-контейнер) обычно тихий —
  весь трафик идёт через stdio `docker exec`.
