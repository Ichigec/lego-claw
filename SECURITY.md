# SECURITY.md — обращение с секретами в lego-claw

> **TL;DR**: в свежеклонированном репо лежат **временные ключи**
> вида `dev-temp-<service>-CHANGE-ME-IN-PRODUCTION-<суффикс>`. Они
> позволяют немедленно поднять стек одной командой и поиграть локально.
> **Перед любым выставлением сервиса наружу (не на 127.0.0.1)
> — обязательно поменяйте все 11 ключей** командой из раздела
> [Ротация одной командой](#ротация-одной-командой).

---

## Что это вообще такое и почему 11 ключей

Каждый «кубик» в `lego-claw` (OpenWebUI, agent-mesh adapters, tool-серверы)
работает в одной docker-сети `llm-stack-net`. Чтобы один контейнер
не мог случайно (или злонамеренно) дёрнуть API другого, на каждый
endpoint навешан **bearer-токен** или **HMAC-secret**. Это локальные
ключи, ничего не имеют общего с OpenAI / Anthropic / Google и не
стоят денег при компрометации — но компрометированный ключ позволит
тому, кто его узнал, использовать вашу OpenWebUI / запускать агентов /
читать-писать в `~/agent_dev/`. Поэтому ротация обязательна.

## Полный список временных ключей

В `.env.example` и `.env.openwebui.example` сразу лежат рабочие
значения. Скопировали — стек поднимается без дополнительных шагов.

### `.env` — agent-mesh adapters + registry (5 ключей)

| Ключ | Где работает | Что защищает | Временное значение |
| --- | --- | --- | --- |
| `CLAWCODE_ADAPTER_API_KEY` | `clawcode-adapter` :8790 (`compose.agents-mesh.yml`) | bearer для `POST /v1/run` — без него LiteLLM / OpenWebUI не могут позвать Claw Code как tool | `dev-temp-clawcode-adapter-CHANGE-ME-IN-PRODUCTION-a1b2c3d4e5` |
| `OPENHANDS_ADAPTER_API_KEY` | `openhands-adapter` :8791 | то же для OpenHands | `dev-temp-openhands-adapter-CHANGE-ME-IN-PRODUCTION-f6g7h8i9j0` |
| `OPENCODE_ADAPTER_API_KEY` | `opencode-adapter` :8792 | то же для opencode | `dev-temp-opencode-adapter-CHANGE-ME-IN-PRODUCTION-k1l2m3n4o5` |
| `AGENT_REGISTRY_API_KEY` | `agent-registry` :8780 | защищает реестр доступных агентов и их моделей | `dev-temp-agent-registry-CHANGE-ME-IN-PRODUCTION-p6q7r8s9t0` |
| `SKILLS_MANAGER_API_KEY` | `skills-manager` :8781 | защищает реестр инструкций / промптов | `dev-temp-skills-manager-CHANGE-ME-IN-PRODUCTION-u1v2w3x4y5` |

### `.env.openwebui` — UI + tool-серверы (6 ключей)

| Ключ | Где работает | Что защищает | Временное значение |
| --- | --- | --- | --- |
| `WEBUI_SECRET_KEY` | OpenWebUI :3000 | подписывает session-cookies; при ротации все пользователи разлогинятся | `dev-temp-webui-secret-CHANGE-ME-IN-PRODUCTION-z6a7b8c9d0e1f2g3h4i5` |
| `SEARXNG_SECRET` | SearXNG :8081 | внутренний HMAC SearXNG (CSRF на form) | `dev-temp-searxng-CHANGE-ME-IN-PRODUCTION-j6k7l8m9n0o1p2q3r4s5` |
| `JUPYTER_TOKEN` | host-side Jupyter `jupyter-host-start.sh` | bearer для kernel-API; используется OpenWebUI Code Interpreter | `dev-temp-jupyter-CHANGE-ME-IN-PRODUCTION-t6u7v8w9x0y1z2a3b4c5` |
| `SHELLBOX_API_KEY` | `shellbox` :8001 (`compose.shellbox.yml`) | bearer для read-only shell-tool | `dev-temp-shellbox-CHANGE-ME-IN-PRODUCTION-d6e7f8g9h0i1j2k3l4m5` |
| `FSBOX_API_KEY` | `fsbox` :8002 (`compose.fsbox.yml`) | bearer для **rw**-filesystem-tool на `~/agent_dev` — потенциально опасный, его меняйте в первую очередь | `dev-temp-fsbox-CHANGE-ME-IN-PRODUCTION-n6o7p8q9r0s1t2u3v4w5` |
| `SEARCHBOX_API_KEY` | `searchbox` :8001 (`compose.searchbox.yml`) | bearer для 15-движкового поиска (включая Google CSE / GitHub API при наличии ключей) | `dev-temp-searchbox-CHANGE-ME-IN-PRODUCTION-x6y7z8a9b0c1d2e3f4g5` |

### Дополнительные поля, которые могут потребоваться

| Поле | Файл | Что делать |
| --- | --- | --- |
| `OPENWEBUI_ADMIN_EMAIL`, `OPENWEBUI_ADMIN_PASSWORD` | `.env.openwebui` | По умолчанию **пустые**. При первом заходе в OpenWebUI назначите админа через web-форму. Если хотите автоматического админа — заполните эти поля до `docker compose up -d openwebui`; OpenWebUI создаст пользователя и отключит signup. |
| `LITELLM_MASTER_KEY` | `.env` (если используется) | По умолчанию `sk-local` (см. `docker/litellm/config.yaml`). Меняйте только если выставляете LiteLLM наружу. |
| `OPENWEBUI_VALIDATE_EMAIL`, `OPENWEBUI_VALIDATE_PASSWORD` | `.env.openwebui` | Используется только в `stack-smoke.sh` для глубоких проверок. По умолчанию пустые; заполните, чтобы smoke-скрипт прошёл `/api/chat/completions`. |

## Что НЕ требует никакой настройки

Ниже секретов нет, потому что эти провайдеры в дефолтном lego-claw
не используются:

- `OPENAI_API_KEY` — нет; LiteLLM ходит в локальные LM Studio /
  llama.cpp / LocalAI / vLLM.
- `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `AZURE_*` — то же.
- `TAVILY_API_KEY`, `GOOGLE_CSE_*`, `SERPAPI_KEY` — упомянуты в docs
  как опция веб-поиска, но не подключены. Подключите явно если нужно.
- LibreChat-секреты (`JWT_SECRET`, `CREDS_KEY`, `MEILI_MASTER_KEY`) —
  отсутствуют, потому что весь LibreChat удалён из snapshot'а.

---

## Ротация одной командой

### Все 11 ключей сразу (рекомендуется)

```bash
# 1) Сделайте локальный .env-файл из шаблона (если ещё нет)
[ -f .env ]          || cp .env.example          .env
[ -f .env.openwebui ] || cp .env.openwebui.example .env.openwebui

# 2) Ротация — копируется команда из README, не редактируется руками
for k in CLAWCODE_ADAPTER_API_KEY OPENHANDS_ADAPTER_API_KEY \
         OPENCODE_ADAPTER_API_KEY AGENT_REGISTRY_API_KEY \
         SKILLS_MANAGER_API_KEY; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env
done

for k in WEBUI_SECRET_KEY SEARXNG_SECRET JUPYTER_TOKEN \
         SHELLBOX_API_KEY FSBOX_API_KEY SEARCHBOX_API_KEY; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env.openwebui
done

# 3) Перезапуск контейнеров, чтобы они подгрузили новые ключи
docker compose -f compose.phoenix.yml -f compose.openwebui.yml \
               -f compose.searxng.yml -f compose.searchbox.yml \
               -f compose.shellbox.yml -f compose.fsbox.yml \
               -f compose.agents-mesh.yml down
bash stack-start.sh
```

### Точечно один ключ

```bash
sed -i "s|^FSBOX_API_KEY=.*|FSBOX_API_KEY=$(openssl rand -hex 32)|" .env.openwebui
docker compose -f compose.fsbox.yml up -d --force-recreate
# и переименуйте Tool Server в OpenWebUI: scripts/openwebui-register-fsbox.sh
```

После ротации `WEBUI_SECRET_KEY` все пользователи OpenWebUI
разлогинятся — это нормально.

После ротации `FSBOX_API_KEY` / `SHELLBOX_API_KEY` / `SEARCHBOX_API_KEY`
нужно пере-зарегистрировать tool servers в OpenWebUI:

```bash
bash scripts/openwebui-register-fsbox.sh
bash scripts/openwebui-register-shellbox.sh
bash scripts/openwebui-register-searchbox.sh
```

---

## Когда **точно** надо ротировать

| Ситуация | Что менять | Срочность |
| --- | --- | --- |
| Выставили любой compose-сервис на `0.0.0.0` (не на `127.0.0.1`) | **Все 11 ключей** | Перед первым `docker compose up` |
| Опубликовали скриншоты / лог-файлы / asciinema, где видны `.env*` | Видимые в скриншоте ключи | Сразу |
| Передали репо коллеге через `tar` (включая `.env*`) | Все 11 ключей у себя и у коллеги | После того как коллега забрал |
| `git push` упал из-за того, что в коммит попал `.env` (см. ниже) | **Все 11 + WEBUI_SECRET_KEY дважды** | Сразу + удалить из git history |
| Просто скопировали `.env.example → .env` и запустили локально на `127.0.0.1` | Не обязательно, но желательно | Когда найдёте 30 секунд |

---

## Как **не допустить утечки** при работе с git

1. **`.env*` уже в `.gitignore`** — никаких `git add .env` случайно. Проверить:
   ```bash
   git check-ignore -v .env .env.openwebui  # должен ответить "ignored"
   ```
2. **Перед каждым `git push`** запускайте `bash scripts/audit-clean.sh` —
   он ищет hex-секреты в `.env*.example`, персональные пути, email'ы.
3. **Если случайно закоммитили `.env`** — недостаточно просто `git rm`.
   Используйте [`git filter-repo`](https://github.com/newren/git-filter-repo)
   или BFG для переписывания истории, и **сразу же ротируйте все 11
   ключей** (то, что было в HEAD на момент `git push`, уже считается
   скомпрометированным).

## Как **не допустить утечки** при работе с чатами AI / IDE

- **Не вставляйте живые ключи в чат с AI-помощниками** (Cursor, Copilot,
  ChatGPT). Перед обсуждением кода всегда подмените секреты на
  `dev-temp-*-CHANGE-ME-*` варианты или на `<REDACTED>`.
- Если случайно вставили — **сразу ротируйте**. Чат-логи нельзя надёжно
  стереть.
- В Cursor у нас стоят `.cursorignore`-правила: `.env*` исключены из
  индексации, но это не страхует от прямой вставки в чат.

---

## Резюме безопасной конфигурации

| Уровень | Что делать |
| --- | --- |
| **Локально, только для себя на `127.0.0.1`** | Можно жить на временных ключах из `.env.example`. Поменяйте `FSBOX_API_KEY` если боитесь, что что-то может подключиться через docker network |
| **На LAN / shared dev машина** | Ротируйте **все 11**. Заполните `OPENWEBUI_ADMIN_*` сильным паролем. Включите `WEBUI_AUTH=true` в `.env.openwebui` |
| **С публичным IP / за reverse-proxy** | Всё из LAN + поставьте TLS на reverse-proxy + `JUPYTER_HOST=127.0.0.1` обязательно (Code Interpreter = remote code execution) + рассмотрите `read_only: true` на `compose.fsbox.yml` |

Если что-то непонятно — лучше задайте вопрос (issue/discussion на GitHub),
чем оставить ключ в default-значении.
