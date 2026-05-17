# LiteLLM: единый шлюз для OpenWebUI, OpenHands и Claw Code

Три клиента ходят в один экземпляр LiteLLM ([`compose.phoenix.yml`](../compose.phoenix.yml)); каталог алиасов — [`docker/litellm/config.yaml`](../docker/litellm/config.yaml).

## Политика дефолтной chat-модели

**Каноническое имя** (один источник правды для людей): переменная `STACK_DEFAULT_LITELLM_CHAT_MODEL` в [`.env`](../.env). Значение должно совпадать с существующим `model_name` в `config.yaml`.

По умолчанию в репозитории выровнено:

| Клиент | Переменная | Значение по умолчанию |
| --- | --- | --- |
| OpenWebUI | `OPENWEBUI_DEFAULT_MODEL` в [`.env.openwebui`](../.env.openwebui) | `qwen3.6-35b-heretic` |
| OpenHands | `OPENHANDS_DEFAULT_MODEL` в [`.env.openhands`](../.env.openhands) | `qwen3.6-35b-heretic` |
| Claw Code | `CLAWCODE_DEFAULT_MODEL` в [`.env.clawcode`](../.env.clawcode) | `openai/qwen3.6-35b-heretic` (Rust `claw` требует `provider/model`; тот же upstream, что у короткого алиаса) |

Алиас `qwen3.6-35b-heretic` в LiteLLM указывает на **host-side llama.cpp** (`LLAMA_CPP_API_BASE`), поэтому чат, GUI-агент и CLI не зависят от того, запущен ли LM Studio.

### Осознанный split (LM Studio vs llama.cpp)

Если нужен другой upstream (например MoE в LM Studio), задайте другой `model_name` в `config.yaml` и **согласованно** обновите только те `.env.*`, которые должны использовать этот алиас. Пример LM Studio: `tvall43-qwen3.6-35b-a3b-heretic` уже объявлен в `config.yaml`.

## OpenHands и `host.docker.internal`

Runtime-песочницы OpenHands сидят на **default bridge** и **не резолвят** DNS `litellm`. База LLM для приложения и песочниц обязана оставаться `http://host.docker.internal:<LITELLM_HOST_PORT>/v1` (см. комментарии в [`compose.openhands.yml`](../compose.openhands.yml)). Менять её на `http://litellm:4000/v1` нельзя — сломает LLM внутри sandbox.

Проверка после правок LiteLLM: `bash stack-smoke.sh` (блок «OpenHands sandbox ↔ LiteLLM») или вручную ephemeral-контейнер с `--add-host=host.docker.internal:host-gateway` и `curl` на тот же URL.

## OpenAI-прокси (pattern B) и agent-mesh адаптеры

В стек добавлен один опциональный сервис в `compose.phoenix.yml`
(`openai-stack-relay`) и два полноценных агент-адаптера в отдельном
`compose.agents-mesh.yml`:

1. **`openai-stack-relay`** — тонкий OpenAI-совместимый HTTP-слой: LiteLLM → relay → LiteLLM с фиксированным `model` (`UPSTREAM_MODEL`). Зарегистрированный алиас: **`stack-openai-relay-qwen36`**. Нужен как шаблон для будущих прокси (оркестратор, очередь, внешний HTTP) без цикла на том же `model_name`.
2. **`clawcode-adapter`** и **`openhands-adapter`** — реальные headless-фронты Claw Code и OpenHands. Поднимаются [`compose.agents-mesh.yml`](../compose.agents-mesh.yml) (см. [`docs/agent-mesh.md`](agent-mesh.md)). Соответствующие LiteLLM-алиасы — `agent/clawcode` и `agent/openhands` — заведены в [`docker/litellm/config.yaml`](../docker/litellm/config.yaml). Старая заглушка `clawcode-cli-routing-stub` и сервис `clawcode-litellm-adapter` удалены: всё реальное теперь делает adapter из agent-mesh.

### Track 2: A2A Agent Gateway

LiteLLM A2A в v1.83.x — [beta-feature](https://docs.litellm.ai/docs/a2a); регистрируется через Admin UI или `POST /model/new`. В этом репозитории мы используем «лёгкий» вариант: прокси-алиасы `agent/clawcode` и `agent/openhands` в `config.yaml` указывают на адаптеры по compose-DNS. Это даёт клиенту единый OpenAI-совместимый интерфейс (`POST /v1/chat/completions` с `model=agent/clawcode`), а LiteLLM с активным A2A-роутером может использовать те же URL как A2A-таргеты без дублирования конфига.

Скрипт [`scripts/litellm-register-agent-mesh.sh`](../scripts/litellm-register-agent-mesh.sh) делает то же без правок yaml — кладёт записи прямо в `litellm-db` через `POST /model/new` (никакого рестарта прокси).

## БД, Admin UI и API против правки `config.yaml` и рестарта

Стек поднимает образ **`ghcr.io/berriai/litellm-database:v1.83.7-stable`** с **`STORE_MODEL_IN_DB=True`** по умолчанию ([`compose.phoenix.yml`](../compose.phoenix.yml)): часть каталога моделей живёт в Postgres (`litellm-db`), а [`docker/litellm/config.yaml`](../docker/litellm/config.yaml) по-прежнему монтируется в контейнер.

| Что меняете | Нужен ли рестарт `litellm` |
| --- | --- |
| Только файл **`config.yaml` на диске** (смонтирован read-only в контейнер) | Обычно **да**: процесс прокси **не** гарантирует подхват yaml «на лету». Надёжный путь — пересоздать/перезапустить сервис `litellm` (или SIGHUP, если проверите поддержку в своей версии — в проде без проверки не полагаться). |
| **Новый `model_name`** (алиас) через **Admin UI** или **HTTP API** при включённом хранении моделей в БД | Часто **нет**: запись уходит в БД и попадает в **`GET /v1/models`** без правки yaml. Сверка контракта для вашей версии: [Model Management](https://docs.litellm.ai/docs/proxy/model_management) (для v1.83.x актуален **`POST /model/new`**; эндпоинты помечены как beta — при обновлении образа перепроверьте доку). |
| **`litellm_settings`**, глобальные **callbacks** (например Phoenix в `config.yaml`), смена **volume** или env, от которых зависит стартовая конфигурация | **Да**: это не «ещё одна строка в каталоге моделей», а изменение того, как поднимается прокси. |

**Аутентификация админ-API:** master key в контейнере задаётся как `LITELLM_MASTER_KEY=${LITELLM_API_KEY}` — в запросах передавайте заголовок **`Authorization: Bearer …`**, подставив значение **`LITELLM_API_KEY`** из [`.env`](../.env).

**Рассинхрон git ↔ БД:** если алиасы добавляли только через UI/API, при следующем деплое из репозитория имеет смысл **дублировать** их в `config.yaml` (или наоборот выровнять БД под yaml), иначе каталог на новой машине и «живой» инстанс разойдутся.

Пример вызова **`POST /model/new`** без ручного сбора JSON: [`scripts/litellm-add-model-curl.sh`](../scripts/litellm-add-model-curl.sh).

## Наблюдаемость

Phoenix callbacks уже настроены в `config.yaml` (`success_callback` / `failure_callback`). Все три клиента попадают в один проект при использовании одного LiteLLM.

## Баннеры OpenWebUI

Ссылки на OpenHands, Claw и LiteLLM UI задаются через `OPENWEBUI_BANNERS` в [`.env.openwebui`](../.env.openwebui). Учитывайте **PersistentConfig**: значения из env применяются при **первом** создании volume OpenWebUI; дальше правки — в UI (`Admin → Settings → Banners`).
