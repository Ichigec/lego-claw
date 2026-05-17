# A2A spec в OpenWebUI Knowledge (`a2a-spec`)

OpenWebUI имеет встроенный RAG (Chroma в `openwebui-data-volume`). Спека
A2A небольшая (~232 KB), отдельный vector DB не нужен — кладём в нативную
knowledge `a2a-spec` и привязываем к модели чата.

Этот документ объясняет: как ингест работает, как переподнять, как
использовать из чата и из bootstrap-скилла `a2a-build-agent`.

## Что делает скрипт

[`scripts/openwebui-ingest-a2a-spec.sh`](../scripts/openwebui-ingest-a2a-spec.sh):

1. **Находит** `uploads/specification-0.md` (или fallback пути), вытаскивает
   `version` (из git short-SHA либо `unknown`).
2. **Нарезает** спеку на ~30 секционных кусков по `^## \d+\.` и
   `^### \d+\.\d+`. Каждый кусок — отдельный `.md` файл в
   `data/a2a-spec-chunks/section-NN-<anchor>.md`.
3. К каждому добавляет YAML-frontmatter:

   ```yaml
   ---
   section: "9. JSON-RPC Transport"
   anchor: "9.4-methods"
   version: "abcdef0"
   source: "uploads/specification-0.md"
   sha256: "<chunk hash>"
   collection: "a2a-spec"
   ---
   ```

4. **Логинится** в OpenWebUI (admin creds из `.env.openwebui`).
5. **Грузит** каждый чанк через `POST /api/v1/files/`.
6. **Создаёт** (или переиспользует) knowledge `a2a-spec` через
   `POST /api/v1/knowledge/create` и **прикрепляет** все file_id через
   `POST /api/v1/knowledge/{id}/file/add`.
7. (Опционально) **Аттачит** knowledge к моделям из env
   `A2A_RAG_ATTACH_TO_MODELS` (default: `qwen3.6-35b-heretic`) через
   admin model API.

Идемпотентно: повторный запуск перезапишет файлы и контент knowledge
без дублей.

## Когда запускается

- Автоматически — последним шагом `stack-start.sh` (после регистрации
  tool-servers).
- Вручную:

  ```bash
  bash scripts/openwebui-ingest-a2a-spec.sh
  ```

  Полезно после обновления `uploads/specification-0.md` — переиндексирует
  всё.

## Переменные окружения

| Переменная | Default | Назначение |
|---|---|---|
| `OPENWEBUI_BASE_URL` | `http://localhost:3000` | OpenWebUI API root. |
| `OPENWEBUI_ADMIN_EMAIL` / `_PASSWORD` | из `.env.openwebui` | для bootstrap JWT. |
| `A2A_SPEC_PATH` | `uploads/specification-0.md` | исходник спеки. |
| `A2A_SPEC_CHUNKS_DIR` | `data/a2a-spec-chunks` | куда писать нарезку. |
| `A2A_RAG_COLLECTION` | `a2a-spec` | имя knowledge в OpenWebUI. |
| `A2A_RAG_ATTACH_TO_MODELS` | `qwen3.6-35b-heretic` | comma-separated модели, к которым аттачить. Пустая — не аттачить. |

## Использование из чата

1. В OpenWebUI откройте чат с моделью `qwen3.6-35b-heretic` (или той, к
   которой аттачнули).
2. Спросите по-русски / по-английски: «Как добавить
   PushNotificationConfig? Покажи методы Section 9.» — LLM сама подтянет
   matching chunks через RAG.
3. Альтернатива — явно подцепить knowledge: в окне чата `➕ → Knowledge
   → a2a-spec`.

## Использование из скиллов

Bootstrap-скилл [`a2a-build-agent`](../.ai/skills/a2a-build-agent/SKILL.md)
содержит инструкцию для агента:

> Когда нужны детали A2A — задай вопрос через RAG-knowledge `a2a-spec`,
> не цитируй спеку по памяти. Указывай Section и anchor из frontmatter
> матчей.

## Версии и инвалидация

- Каждый chunk имеет `version` (git SHA) и `sha256` в frontmatter.
- При повторном `bash scripts/openwebui-ingest-a2a-spec.sh` файлы с тем
  же `sha256` пропускаются (нет смысла переподнимать векторы).
- Если меняется `uploads/specification-0.md` — bump git, прогон, готово.

## Диагностика

| Симптом | Лечение |
|---|---|
| LLM не цитирует спеку | проверь, что knowledge привязана: Admin → Models → `qwen3.6-35b-heretic` → Knowledge → должна быть `a2a-spec`. Или прикрепи руками в чате. |
| `401 Unauthorized` при загрузке файлов | сверь `OPENWEBUI_ADMIN_EMAIL/PASSWORD` в `.env.openwebui`. |
| `data/a2a-spec-chunks/` пуст | спеку не нашёл; задай `A2A_SPEC_PATH=...`. |
| Дубли в коллекции | API не отдаёт `clear` — удали knowledge в Admin → Knowledge и переподними скрипт. |

См. также:

- [`docs/agent-mesh.md`](agent-mesh.md) — где `a2a-spec` лежит в общей картине.
- [`docs/skills.md`](skills.md) — bootstrap-скилл `a2a-build-agent`.
- [`docs/openwebui.md`](openwebui.md), [`docs/openwebui-tools.md`](openwebui-tools.md)
  — общая работа с OpenWebUI knowledge / tools.
