# login.md — где и как логиниться во все приложения lego-claw

> Все URL'ы — на `127.0.0.1`/`localhost` (loopback). Для удалённого доступа
> ставьте reverse-proxy с TLS и сначала ротируйте все ключи —
> см. [`SECURITY.md`](SECURITY.md).
>
> Все «секреты» в этом файле — это **переменные** (`$WEBUI_SECRET_KEY` и т.д.),
> которые нужно подсмотреть в своих локальных `.env*` (gitignored).
> В git мы храним только `.env*.example`-шаблоны с `dev-temp-…` плейсхолдерами.

---

## TL;DR — главная карта

| # | Сервис | URL | Кто логинится / как | Где взять креды |
| - | --- | --- | --- | --- |
| 1 | **OpenWebUI** | http://localhost:3000 | email + password | задаются при первом заходе ИЛИ через `OPENWEBUI_ADMIN_EMAIL` / `OPENWEBUI_ADMIN_PASSWORD` в `.env.openwebui` |
| 2 | **LiteLLM Admin UI** | http://localhost:4000/ui | username + password | `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD` в `.env` (default `admin` / `litellm-local-ui`) |
| 3 | **Phoenix** (трейсы) | http://localhost:6006 | без логина | — (только из `llm-stack-net`) |
| 4 | **OpenHands** | http://localhost:3300 | без логина | LLM-настройки предзаполнены в `~/.openhands/settings.json` (см. `openhands-start.sh` шаг 5.1) |
| 5 | **opencode Web UI** | http://localhost:3400 | без логина | поднимается `bash opencode-start.sh --web` |
| 6 | **Dify** (опц.) | http://localhost:8090 | email + password (создаётся через `/install`) | первый зашедший = admin; затем Console Token в Settings → Profile → API Keys |
| 7 | **SearXNG** | http://localhost:8081 | без логина | (внутренний CSRF-secret `SEARXNG_SECRET` в `.env.openwebui`) |
| 8 | **Jupyter** (host-side) | http://localhost:8888/?token=… | URL-token | `JUPYTER_TOKEN` в `.env.openwebui`; стартует `jupyter-host-start.sh` |
| 9 | **LiteLLM master key** | API: http://localhost:4000/v1 | bearer | `LITELLM_API_KEY` в `.env` (default `sk-local`) |

---

## 1. OpenWebUI — главный чат

### URL и креды

```
http://localhost:3000
```

**Сценарий A — пустая база (первый запуск):**

- В `.env.openwebui` оставлены пустыми `OPENWEBUI_ADMIN_EMAIL` /
  `OPENWEBUI_ADMIN_PASSWORD`. → При первом заходе OpenWebUI покажет
  форму регистрации, **первый созданный** аккаунт станет admin'ом.
  После этого signup можно отключить (`OPENWEBUI_ENABLE_SIGNUP=false`).

**Сценарий B — bootstrap admin'а headless'ом (CI / shared hosts):**

В `.env.openwebui`:
```bash
OPENWEBUI_ADMIN_NAME=OpenWebUI-Admin
OPENWEBUI_ADMIN_EMAIL=admin@example.org
OPENWEBUI_ADMIN_PASSWORD=<сильный_пароль>
```

При первом запуске на чистом volume OpenWebUI создаст admin'а с
этими кредами и **отключит signup**. На существующий volume не
действует — менять admin'а через UI.

### Где смотреть пароль / получить JWT программно

```bash
# 1) Pre-baked JWT, сгенерированный из контейнера (используется
#    scripts/openwebui-register-*.sh, см. также docs/openwebui-tools.md):
docker exec open-webui python3 -c "
import sqlite3, datetime as dt, importlib
con = sqlite3.connect('/app/backend/data/webui.db')
uid = con.execute(\"SELECT id FROM user WHERE role='admin' LIMIT 1\").fetchone()[0]
mod = importlib.import_module('open_webui.utils.auth')
print(mod.create_token({'id': uid}, expires_delta=dt.timedelta(hours=1)))
"
```

### Опционально (для smoke-тестов)

```bash
OPENWEBUI_VALIDATE_EMAIL=admin@example.org
OPENWEBUI_VALIDATE_PASSWORD=<тот_же_что_OPENWEBUI_ADMIN_PASSWORD>
```

Их использует `stack-smoke.sh` для глубокого `/api/chat/completions`
прохода. Если пустые — глубокий чек пропускается.

---

## 2. LiteLLM Admin UI

```
http://localhost:4000/ui
```

| Поле | Значение | Откуда |
| --- | --- | --- |
| Username | `LITELLM_UI_USERNAME` (default `admin`) | `.env` |
| Password | `LITELLM_UI_PASSWORD` (default `litellm-local-ui`) | `.env` |

### Master-key для API (не путать с UI-логином!)

Защищает все вызовы `POST /v1/chat/completions`, `POST /model/new`,
`GET /model/info` и т.д. на `:4000/v1`:

```
LITELLM_API_KEY=sk-local      # default; ротируйте перед публикацией
```

Используется как `Authorization: Bearer $LITELLM_API_KEY` в:
- `curl http://localhost:4000/v1/models`
- `OPENWEBUI_OPENAI_API_KEYS` в `.env.openwebui`
- `OPENHANDS_LITELLM_API_KEY` (или fallback на `LITELLM_API_KEY`)
- Все `*-adapter` контейнерные клиенты

---

## 3. Phoenix (трейсы LiteLLM)

```
http://localhost:6006
```

Без логина — Phoenix UI отдаёт всё анонимно. Изоляция = только
loopback-биндинг + `llm-stack-net`. Если выставляете наружу —
ставьте reverse-proxy с базовой авторизацией.

---

## 4. OpenHands GUI

```
http://localhost:3300
```

OpenHands не имеет своей системы пользователей — это однопользовательский
GUI. Настройки LLM (model, base_url, api_key) предзаполняются нашим
`openhands-start.sh` в `~/.openhands/settings.json` при первом запуске:

```json
{
  "llm_model": "litellm_proxy/qwen3.6-35b-heretic",
  "llm_base_url": "http://host.docker.internal:4000/v1",
  "llm_api_key": "<LITELLM_API_KEY>",
  "agent": "CodeActAgent",
  "language": "ru",
  "confirmation_mode": false
}
```

> ⚠ **Sandbox**, который OpenHands спавнит для каждой сессии, ходит
> в LiteLLM через `host.docker.internal:4000`, а **не** через compose-DNS
> `litellm`. См. `docs/litellm-clients.md` → «OpenHands и host.docker.internal».

Поменять в UI: **Settings → LLM → Advanced** (перепишет файл выше).

---

## 5. opencode Web UI

```
http://localhost:3400      # bash opencode-start.sh --web
```

Без логина (loopback only). LLM-настройки в `~/.opencode/`:

- Default-model берётся из `OPENCODE_DEFAULT_MODEL` в `.env.opencode`
  (default: `litellm/qwen3.6-35b-heretic`).
- Base URL: `OPENCODE_LITELLM_BASE_URL` (default
  `http://litellm:4000/v1` через compose-DNS).
- Bearer: `OPENCODE_LITELLM_API_KEY` ⇨ fallback на `LITELLM_API_KEY`.

В TUI режиме (`bash opencode-start.sh` без `--web`) то же самое:
opencode читает свой `~/.opencode/auth.json` из персистентного
`OPENCODE_STATE_DIR`.

---

## 6. Dify (опциональный кубик)

```
http://localhost:8090
```

### 6.1. Первый запуск — создать admin'а

1. Откройте http://localhost:8090. На свежем volume URL автоматически
   редиректит на `/install` — форма создания первого admin'а.
2. Email + password. **Сохраните их** — это единственный admin
   workspace'а, кнопки «забыл пароль» нет (в self-host инсталляции).
3. После создания → редирект на `/signin` → залогиньтесь.

### 6.2. Console Token (для авторегистрации provider'ов / tool'ов)

После логина:

1. Кликнуть по аватару (правый верхний угол) → **Settings**.
2. Profile → **API Keys** → **Create new** → скопировать (формат
   `app-XXXXXXXXXXXXX...`).
3. Положить в `.env.dify`:
   ```bash
   DIFY_CONSOLE_TOKEN=app-XXXXXXXX...
   ```
4. Теперь скрипты работают:
   ```bash
   bash scripts/dify-register-litellm.sh
   bash scripts/dify-register-agent-mesh.sh
   ```

### 6.3. Headless-логин (опц.)

Если admin создан через UI, можно логиниться скриптами через
`POST /console/api/login` — для этого пропишите в `.env.dify`:

```bash
DIFY_ADMIN_EMAIL=admin@example.org
DIFY_ADMIN_PASSWORD=<тот_же_что_в_UI>
```

`scripts/dify-register-*.sh` сами получат JWT, если `DIFY_CONSOLE_TOKEN`
пуст.

### 6.4. Upstream-секреты Dify (отдельно от наших ключей)

Живут в `dify/docker/.env` (gitignored, генерируется
`dify-start.sh` из upstream `.env.example`). Перед выставлением Dify
наружу — поменяйте `SECRET_KEY` и `CODE_EXECUTION_API_KEY`. Подробнее
в [`SECURITY.md`](SECURITY.md) → «Dify секреты».

---

## 7. SearXNG

```
http://localhost:8081
```

Без логина. Внутренний CSRF-secret для form-submit:

```
SEARXNG_SECRET     # .env.openwebui, ротируйте openssl rand -hex 32
```

Используется только самим SearXNG'ом, наружу не торчит.

---

## 8. Jupyter (host-side)

```
http://127.0.0.1:8888/?token=$JUPYTER_TOKEN
```

| Поле | Значение |
| --- | --- |
| Token | `JUPYTER_TOKEN` в `.env.openwebui` |
| Bind | только `127.0.0.1:8888` (`JUPYTER_HOST=127.0.0.1`) |
| Workspace | `JUPYTER_ROOT_DIR` (default `${HOME}/agent_dev`) |

OpenWebUI ходит сюда через `host.docker.internal:8888` (см.
`OPENWEBUI_CODE_EXECUTION_JUPYTER_URL` в `.env.openwebui`),
шапка `Authorization: token $JUPYTER_TOKEN`.

Запуск: `bash jupyter-host-start.sh`. Ручной заход в браузере:
URL ↑ с подставленным токеном.

---

## 9. Bearer-токены OpenAPI tool-серверов (под капотом OpenWebUI)

Эти токены пользователь не вводит руками — их использует OpenWebUI
при дёргании tool'ов. Регистрируются `scripts/openwebui-register-*.sh`.
Все 6 хранятся в `.env`/`.env.openwebui`:

| Переменная | Защищает endpoint | Что регистрирует |
| --- | --- | --- |
| `SEARCHBOX_API_KEY` | http://searchbox:8001 | `bash scripts/openwebui-register-searchbox.sh` |
| `SHELLBOX_API_KEY` | http://shellbox:8001 | `bash scripts/openwebui-register-shellbox.sh` |
| `FSBOX_API_KEY` | http://fsbox:8002 | `bash scripts/openwebui-register-fsbox.sh` |
| `CLAWCODE_ADAPTER_API_KEY` | http://clawcode-adapter:8790 | `bash scripts/openwebui-register-agent-mesh.sh` |
| `OPENHANDS_ADAPTER_API_KEY` | http://openhands-adapter:8791 | то же |
| `OPENCODE_ADAPTER_API_KEY` | http://opencode-adapter:8798 | то же |
| `AGENT_REGISTRY_API_KEY` | http://agent-registry:8780 | `bash scripts/openwebui-register-agent-registry.sh` |
| `SKILLS_MANAGER_API_KEY` | http://skills-manager:8781 | (вызывается агентами через MCP) |

**Если ротировали ключ** в `.env`/`.env.openwebui`, нужно:
1. Пересоздать соответствующий контейнер: `docker compose -f compose.<name>.yml up -d --force-recreate`.
2. Заново вызвать `bash scripts/openwebui-register-<name>.sh` (он перезальёт ключ в `tool_servers` config OpenWebUI).

**Симптом «не зашло»** — в логах OpenWebUI:
```
ERROR open_webui.utils.tools:get_tool_server_data — Could not fetch tool server spec ... 401
```
→ ключ в OpenWebUI-стороне НЕ совпадает с тем, что в контейнере
adapter'а (типичный случай: ротировали `.env`, не пересоздали
контейнер). Лекарство ↑ из таблицы.

---

## 10. Утилиты для извлечения текущих кредов

Все примеры ниже **читают существующий `.env*`**, не показывая полные
значения в выводе:

```bash
cd /home/pavel/cursor/first

# Все логины UI одной командой
echo "OpenWebUI admin = ${OPENWEBUI_ADMIN_EMAIL:-(см. форму при входе)}"
grep -hE '^(LITELLM_UI_USERNAME|LITELLM_UI_PASSWORD)=' .env

# Прокинуть Jupyter-токен в текущую сессию shell'а
eval "$(grep '^JUPYTER_TOKEN=' .env.openwebui)"
echo "http://127.0.0.1:8888/?token=$JUPYTER_TOKEN"

# Сгенерировать одноразовый JWT OpenWebUI
docker exec open-webui python3 -c "
import sqlite3, datetime as dt, importlib
con = sqlite3.connect('/app/backend/data/webui.db')
uid = con.execute(\"SELECT id FROM user WHERE role='admin' LIMIT 1\").fetchone()[0]
mod = importlib.import_module('open_webui.utils.auth')
print(mod.create_token({'id': uid}, expires_delta=dt.timedelta(hours=1)))
"
```

Если вы потеряли OpenWebUI-пароль и admin создавался через UI:

```bash
# nuke admin пароль (возьмёт OPENWEBUI_ADMIN_EMAIL/PASSWORD из .env.openwebui
# на следующем рестарте)
docker exec open-webui python3 -c "
import sqlite3
con = sqlite3.connect('/app/backend/data/webui.db')
con.execute(\"DELETE FROM auth WHERE id=(SELECT id FROM user WHERE role='admin' LIMIT 1)\")
con.commit()
"
docker compose -f compose.openwebui.yml up -d --force-recreate openwebui
```

---

## 11. Куда смотреть, если ничего не подходит

| Симптом | Куда |
| --- | --- |
| Открывается чужой пустой UI | Скорее всего сидите на дефолтных `dev-temp-*` ключах — это нормально; см. `SECURITY.md` |
| `401 Unauthorized` от tool-сервера | Ключ в OpenWebUI ≠ ключ в контейнере; § 9 этого файла |
| `Invalid or expired token` от LiteLLM | `LITELLM_API_KEY` в `.env` поменяли, но в `.env.openwebui` (`OPENWEBUI_OPENAI_API_KEYS`) старое значение |
| OpenHands не пускает к LLM | Откройте Settings → LLM, проверьте `Custom Model` = `litellm_proxy/qwen3.6-35b-heretic`, `Base URL` = `http://host.docker.internal:4000/v1` |
| Dify «can't find provider» | Не запускали `bash scripts/dify-register-litellm.sh` после `dify-start.sh` |
| Jupyter 403 / Forbidden | Token в URL не совпадает с `JUPYTER_TOKEN`; либо kernel запущен от другого юзера |

См. также:
- [`SECURITY.md`](SECURITY.md) — все 11 наших ключей + Dify-секреты,
  ротация одной командой
- [`README.md`](README.md) → «Безопасность»
- [`INSTALL.md`](INSTALL.md) → «Карта тумблеров»
