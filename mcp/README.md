# searchbox — мульти-поисковый MCP-сервер

`mcp/` — это исходники локального MCP-сервера, который отдаёт единый
tool `search` поверх 15 разных источников. Картинка зависимостей
целиком помещается в два предложения:

- **OpenWebUI** получает его как OpenAPI-тул через `mcpo` на `:8001`.
- **OpenHands / Cursor / Claude Desktop** — как нативный MCP через
  Streamable HTTP (`/mcp`) или SSE (`/sse`) на `:8090`.

Основной рантайм — контейнер `searchbox` из
[`compose.searchbox.yml`](../compose.searchbox.yml). Запуск на хосте
без Docker — тоже поддерживается (см. «Запуск вне Docker»).

## Архитектура

```
┌────────────┐   OpenAPI :8001            ┌──────────────────┐
│ OpenWebUI  │──────────────────────────▶│                  │
└────────────┘   (mcpo bearer token)      │                  │
┌────────────┐   MCP SSE :8090            │   /app/server.py │
│ OpenHands  │──────────────────────────▶│   (FastMCP)      │
└────────────┘                            │                  │
┌────────────┐   stdio                    │                  │
│  Cursor /  │──────────────────────────▶│                  │
│ Claude Dt. │                            └─────────┬────────┘
└────────────┘                                      │
                                        ┌───────────┴───────────┐
                                        │  engines/ registry    │
                                        │  (15 шт.)             │
                                        └───────────┬───────────┘
                                                    │
   ┌───────────┬────────────┬────────────┬───────────┼────────────┬────────────┬────────────┐
   ▼           ▼            ▼            ▼           ▼            ▼            ▼            ▼
 SearXNG  DuckDuckGo  Wikipedia   Wikidata     arXiv        GitHub     HackerNews  StackExchange
   │           │           │            │           │           │            │            │
   ▼           ▼           ▼            ▼           ▼           ▼            ▼            ▼
 Crossref  OpenAlex     PyPI         NPM        Brave       Google       Tavily        (+...)
```

## Источники

| Engine           | Ключ                              | По умолчанию |
| ---------------- | --------------------------------- | ------------ |
| `searxng`        | `SEARXNG_URL` (default — локальный контейнер) | ✅ on |
| `duckduckgo`     | —                                 | ✅ on |
| `wikipedia`      | —                                 | ✅ on |
| `wikidata`       | —                                 | ✅ on |
| `arxiv`          | —                                 | ✅ on |
| `github`         | `GITHUB_TOKEN` (опц. — для rate-limit) | ✅ on |
| `hackernews`     | —                                 | ✅ on |
| `stackexchange`  | `STACKEXCHANGE_KEY` (опц.)        | ✅ on |
| `crossref`       | —                                 | ✅ on |
| `openalex`       | `OPENALEX_MAILTO` (опц.)          | ✅ on |
| `pypi`           | —                                 | ✅ on |
| `npm`            | —                                 | ✅ on |
| `brave`          | `BRAVE_API_KEY`                   | ⚙️ off  |
| `google`         | `GOOGLE_API_KEY` + `GOOGLE_CX`    | ⚙️ off  |
| `tavily`         | `TAVILY_API_KEY`                  | ⚙️ off  |

## MCP-tools

Регистрируются в [`server.py → register_tools`](server.py):

- `search(query, engines?, max_results, per_engine_timeout)` — умный
  fan-out. Если `engines` не указан — идёт во все доступные
  параллельно. Возвращает `{query, engines_used, errors, results,
  per_engine}`: `results` — merged round-robin список с дедупом по
  нормализованному URL, `per_engine` — сырые результаты для отладки.
- `search_<engine>(query, max_results)` — индивидуальный tool под
  каждый движок. Пример: `search_github`, `search_arxiv`.
- `list_engines()` — полный список движков с описанием и полем
  `available` (нужно LLM, чтобы выбрать источник).
- `search_status()` — готовность каждого движка (какие env нужны).

Дедуп и мердж живут в [`multi.py`](multi.py). Алгоритм:
`asyncio.gather(..., return_exceptions=False)` с per-engine
`asyncio.timeout(...)`, затем round-robin по порядку движков с
пропуском уже видимых URL. Round-robin выбран специально вместо RRF —
score'ы разных источников (GitHub stars vs HN points vs SearXNG
relevance) несопоставимы, и честнее дать каждому шанс первого места.

## Переменные окружения

Все ключи читаются в [`engines/base.py`](engines/base.py) и per-engine
модулях из `os.environ`. Значения по умолчанию рассчитаны на запуск в
контейнере на `llm-stack-net`:

| Var                         | Default                     | Назначение |
| --------------------------- | --------------------------- | ---------- |
| `SEARXNG_URL`               | `http://searxng:8080`       | URL локального SearXNG |
| `SEARCHBOX_HTTP_TIMEOUT`    | `10`                        | Общий HTTP-timeout на движок |
| `SEARCHBOX_USER_AGENT`      | `searchbox-mcp/...`         | UA для всех исходящих |
| `SEARCHBOX_LOG_LEVEL`       | `INFO`                      | logger level |
| `SEARCHBOX_TRANSPORT`       | `stdio`                     | `stdio` / `http` / `sse` |
| `SEARCHBOX_HOST`            | `0.0.0.0`                   | Bind для HTTP/SSE |
| `SEARCHBOX_PORT`            | `8090`                      | Порт для HTTP/SSE |
| `SEARCHBOX_ENGINES`         | —                           | Whitelist движков (csv) |
| `BRAVE_API_KEY`             | —                           | Включает `brave` |
| `GOOGLE_API_KEY` + `GOOGLE_CX` | —                        | Включает `google` |
| `TAVILY_API_KEY`            | —                           | Включает `tavily` |
| `GITHUB_TOKEN`              | —                           | Повышает rate-limit `github` |
| `STACKEXCHANGE_KEY`         | —                           | Повышает rate-limit SE |
| `OPENALEX_MAILTO`           | —                           | polite-pool OpenAlex |

## Запуск

### Через Docker (рекомендуется)

```bash
# Поднимает SearXNG (если ещё нет) и searchbox.
docker compose -f compose.searxng.yml -f compose.searchbox.yml up -d --build
```

После старта:

```bash
# OpenAPI-эндпоинт, дёргаемый OpenWebUI — из .env.openwebui
curl -fsS -H "Authorization: Bearer $SEARCHBOX_API_KEY" \
    http://127.0.0.1:8023/openapi.json | jq '.paths | keys'

# Нативный MCP SSE — используется OpenHands
curl -fsS http://127.0.0.1:8024/sse  # -> event stream
```

### Запуск вне Docker

```bash
cd mcp
pip install -e .
python3 server.py --transport http --port 8090
# или stdio для Cursor/Claude Desktop:
python3 server.py --transport stdio
```

Сниппет `mcpServers`-конфига для Cursor / Claude Desktop —
[`mcp_config.json`](mcp_config.json). Для OpenHands —
[`openhands_mcp.json`](openhands_mcp.json).

### Ограничить набор движков

```bash
python3 server.py --engines searxng,wikipedia,arxiv
# или env-ом: SEARCHBOX_ENGINES=searxng,wikipedia python3 server.py
```

Полезно для stdio-клиентов с маленьким tool-budget (Claude Desktop
показывает tool в автокомплите один раз на каждый MCP-инстанс).

## OpenWebUI

Через `mcpo`-обёртку и `TOOL_SERVER_CONNECTIONS`:

1. В `.env.openwebui` заполнены `SEARCHBOX_API_KEY`, `SEARCHBOX_HOST_PORT`,
   `SEARCHBOX_MCP_HOST_PORT`.
2. `stack-start.sh` пре-сидит запись `searchbox` в
   `TOOL_SERVER_CONNECTIONS` при первом запуске свежего volume.
3. На уже живом инстансе — `bash scripts/openwebui-register-searchbox.sh`.
4. В чате: `+` → включить `searchbox` → спрашивать.

Подробнее — [`docs/openwebui-tools.md → 5. Multi-engine Search`](../docs/openwebui-tools.md#5-multi-engine-search-searchbox).

## OpenHands

OpenHands 1.6 читает нативный MCP через UI:
`Settings → MCP → Add Server` → URL `http://host.docker.internal:8024/sse`
(runtime-sandbox'ы живут в default bridge-сети и compose-DNS не видят).
Snippet — [`openhands_mcp.json`](openhands_mcp.json), разбор — в
[`docs/openhands.md → OpenHands → локальный MCP-Search`](../docs/openhands.md).

## Разработка

```bash
cd mcp
${HOME}/lego-claw/.venv/bin/python -m pytest tests/ -v
```

Тесты мокают весь HTTP через `respx`; реальной сети не требуется.
Новый движок:

1. Создать `engines/<name>.py`, унаследоваться от `SearchEngine`, в конце
   файла — `register(<cls>())`.
2. Добавить имя в `_BUILTIN_ENGINES` в `engines/__init__.py`.
3. Покрыть happy-path и один error-case в `tests/test_engines.py`.

## Troubleshooting

- `"engine is not available"` — нет нужного env-ключа. Список
  обязательных env'ов возвращает `search_status`.
- Тихие пустые результаты — включите `SEARCHBOX_LOG_LEVEL=DEBUG` и
  смотрите `docker logs searchbox`.
- `searchbox` не отвечает на `/openapi.json` — проверьте, что в
  контейнере есть сеть `llm-stack-net` и SearXNG доступен по
  `http://searxng:8080/search?q=test&format=json`.
- Таймауты gather-фазы — `SEARCHBOX_HTTP_TIMEOUT=20` (секунд) и/или
  параметр `per_engine_timeout` в самом tool-вызове.

## Что считается «плохим результатом»

- Любой `ERR 5xx` у конкретного движка: попадает в `errors`, остальные
  движки продолжают работать — `multi_search` намеренно не пробрасывает
  такие ошибки наверх.
- Движок, не настроенный по env (`brave` без `BRAVE_API_KEY`):
  `available=False`; если его запросили явно — ошибка «engine is not
  available» в `errors`.
- Пустой SearXNG — обычно результат неправильного `SEARXNG_URL` или
  что SearXNG ещё не прогрел redis-кэш (`docker logs searxng`).
