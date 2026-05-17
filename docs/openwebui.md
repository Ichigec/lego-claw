# OpenWebUI

`OpenWebUI` is now a first-class UI in this stack.

It runs from `compose.openwebui.yml`, joins `llm-stack-net`, stores state in
`openwebui-data-volume`, discovers chat models through `LiteLLM`, and now also
routes `STT/TTS` through `LiteLLM`, which proxies audio requests to `LocalAI`.

## Runtime wiring

- Host URL: `http://localhost:3000`
- Model gateway: `http://litellm:4000/v1`
- Default model: `qwen3.6-35b-heretic`
- Default locale: `ru-RU`
- Audio gateway: `http://litellm:4000/v1`
- Audio upstream: `http://localai:8080/v1`
- Persistent data: Docker volume `openwebui-data-volume`
- OpenWebUI-specific env: `.env.openwebui`

The stack scripts read both `.env` and `.env.openwebui` automatically before
calling Docker Compose, so `OPENWEBUI_HOST_PORT`, `WEBUI_SECRET_KEY`, provider
settings, and optional validation credentials stay outside the older shared
`.env`, while shared stack ports and backend selectors still come from `.env`.

## Start and smoke-test

Bring the whole stack up:

```bash
bash ./stack-start.sh
```

Run the rollout smoke-test:

```bash
bash ./stack-smoke.sh
```

Run the Russian-language demo walkthrough:

```bash
bash ./stack-demo-ru.sh
```

If you want to launch only OpenWebUI manually, make sure `LiteLLM` and
`LocalAI` are already reachable on `llm-stack-net`, then export the
OpenWebUI-specific env first:

```bash
set -a
. ./.env.openwebui
set +a
docker compose --env-file ./.env -f compose.openwebui.yml up -d
```

## Russian demo

`OpenWebUI` now defaults to `ru-RU` through `OPENWEBUI_DEFAULT_LOCALE=ru-RU`,
so a fresh demo session opens in Russian without extra clicks.

Use this flow for a short live demo:

1. Start the stack with `bash ./stack-start.sh`.
2. Run `bash ./stack-demo-ru.sh`.
3. Open `http://localhost:3000` and confirm the interface is in Russian.
4. Pick `qwen3.6-35b-heretic` and send a Russian prompt, for example:
   - `Кратко объясни, как запрос проходит через OpenWebUI, LiteLLM и Phoenix.`
   - `Составь три идеи голосового ассистента для офиса.`
5. Use the microphone or TTS controls to show the `LiteLLM -> LocalAI` audio path.
6. Open `http://localhost:6006` and show the new span in Phoenix.

If the browser still shows English, the old locale is usually cached already.
Open a private window or switch `Settings -> General -> WebUI Settings ->
Language -> Russian` once.

## Admin bootstrap

Default flow: open `http://localhost:3000` and create the first user in the UI.
OpenWebUI promotes the first account to admin.

For headless/bootstrap runs on a fresh OpenWebUI data volume, set these in
`.env.openwebui` before the first start:

- `OPENWEBUI_ADMIN_EMAIL`
- `OPENWEBUI_ADMIN_PASSWORD`
- optional `OPENWEBUI_ADMIN_NAME`

When those are set, OpenWebUI creates the admin user on first boot and disables
sign-up automatically.

`stack-smoke.sh` also supports deeper authenticated checks. It will use
`OPENWEBUI_VALIDATE_EMAIL` and `OPENWEBUI_VALIDATE_PASSWORD` when present, or
fall back to the admin credentials above.

## Audio policy

`OpenWebUI` audio идёт через `LiteLLM` → `LocalAI` v4. В LocalAI v4
бэкенды (whisper, qwen-tts, fish-speech, piper, silero-vad) — отдельные
OCI-plugin образы, ставятся через gallery API. Установка
зашита в [`localai-start.sh`](../localai-start.sh) →
`ensure_backends()` и идемпотентна (повторный запуск пропускает уже
установленные).

| Layer       | Default alias (LiteLLM)         | LocalAI model                       | LocalAI backend (ARM64 + CUDA-13)              | Voice          |
| ----------- | ------------------------------- | ----------------------------------- | ---------------------------------------------- | -------------- |
| STT         | `stt-whisper-cuda-turbo`        | `whisper-large-turbo-q5_0`          | `cuda13-nvidia-l4t-arm64-whisper` (GPU)        | —              |
| TTS default | `tts-ru-default`                | `qwen3-tts-1.7b-custom-voice`       | `cuda13-nvidia-l4t-arm64-qwen-tts` (GPU)       | `ono_anna`     |
| TTS alt     | `tts-ru-alt`                    | `qwen3-tts-0.6b-custom-voice`       | `cuda13-nvidia-l4t-arm64-qwen-tts` (GPU)       | `ono_anna`     |
| TTS fallback| `tts-piper-ru-fallback`         | `voice-ru_RU-irina-medium`          | `piper` (universal, CPU; instant TTFB)         | (any)          |
| VAD         | browser-side (OpenWebUI Voice Mode) | `silero-vad` (LocalAI direct only) | `silero-vad` (universal CPU)               | —              |

Замеры на DGX Spark (GB10) когда LM Studio занимает ~73 ГБ unified
memory:

- `stt-whisper-cuda-turbo` — cold ~4 с, hot ~1 с на короткую фразу.
- `tts-ru-default` (Qwen3-TTS-1.7B) — cold ~41 с, hot ~2.7 с через LocalAI
  и ~3.2 с через LiteLLM. Занимает ~4.4 ГБ VRAM.
- `tts-ru-alt` (Qwen3-TTS-0.6B) — cold ~28 с (загрузка модели),
  hot ~2.9 с через LocalAI и ~5.5 с через LiteLLM. ~2.6 ГБ VRAM,
  компактнее и без вокализных артефактов, но звучит более «роботизированно».
- `tts-piper-ru-fallback` — TTFB ~2.6 с (CPU, без cold-start).

A/B-победитель на 18-словном RU-тексте — **1.7B**: голос ближе к
human-like. Каверзный артефакт: на длинных фразах модель иногда
вставляет вокализы вроде «Эх...» в начале. Если критично — переключайте
на `tts-ru-alt` (0.6B читает буквальнее) или `tts-piper-ru-fallback`
(гарантировано без галлюцинаций).

#### Почему раньше 1.7B падал с CUDA OOM

PyTorch caching allocator под ARM L4T (Jetson / GB10) **резервирует
большой непрерывный slab** при первой инициализации CUDA контекста.
Когда LM Studio уже держит 73 ГБ unified memory, оставшиеся 17 ГБ
фрагментированы — slab не выделяется и `cudaMalloc` валится с
`out of memory`, хотя свободного места формально хватает.

Фикс — env-переменная в [`compose.localai.yml`](../compose.localai.yml):

```yaml
- PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512,garbage_collection_threshold:0.6
```

`expandable_segments:True` заставляет аллокатор расти инкрементально
вместо предварительной резервации, `max_split_size_mb:512` режет крупные
блоки на части, которые легче разместить во фрагментированном пуле.

Список встроенных голосов Qwen3-TTS-CustomVoice (одинаковый для 0.6B/1.7B):

```
aiden, dylan, eric, ono_anna, ryan, serena, sohee, uncle_fu, vivian
```

Дефолт `ono_anna` — нейтральный женский, лучше всех читает русские
слова без явного акцента. Менять можно per-user (`User Settings → Audio →
TTS Voice`) или per-model (`Workspace → Models → Edit → TTS Voice`).

Deprecated, оставлены ради backwards-compat существующих чатов:

- `stt-whisper-large-v3-turbo` (whisper.cpp non-quantized, тот же backend но больший файл)
- `tts-qwen3-1.7b` (старая YAML-регистрация с тем же `qwen-tts` backend, оставлена как fallback alias)

### Известные проблемы upstream

- **`fish-speech-1.5`** установлен (`docker/localai/models/tts-fish-speech.yaml`,
  backend `cuda13-nvidia-l4t-arm64-fish-speech`), но падает при загрузке:
  `Failed to load model: No module named 'fish_speech.inference_engine'`.
  Это баг в текущем backend-image LocalAI v4.1.3 для ARM64. Алиас
  `tts-ru-fish` закомментирован в [`docker/litellm/config.yaml`](../docker/litellm/config.yaml),
  ждём обновления плагина.
- **LiteLLM требует `voice` в `/v1/audio/speech`** даже когда модель
  берёт voice из своего имени. OpenWebUI это делает автоматически из
  `OPENWEBUI_AUDIO_TTS_VOICE`; для curl-тестов всегда передавайте
  `"voice":"..."` в JSON-payload.
- **Per-user voice override перекрывает Admin Panel.** OpenWebUI хранит
  выбор голоса не только в `config.audio.tts.voice`, но и в каждой
  записи `user.settings.ui.audio.tts.{voice,defaultVoice}`. Если
  пользователь хоть раз открывал `User Settings → Audio`, в БД
  закрепляется его персональный голос (например legacy
  `ru_RU-irina-medium` от piper-fallback). При переключении
  TTS-модели на `qwen-tts` бэкенд возвращает 500 с понятным
  `Unsupported speakers: ['ru_RU-irina-medium']. Supported: ['aiden',
  'dylan', 'eric', 'ono_anna', ...]`. Чинится массовым патчем:

  ```bash
  docker exec open-webui python3 - <<'PY'
  import sqlite3, json, time
  con = sqlite3.connect('/app/backend/data/webui.db')
  con.execute(f"VACUUM INTO '/tmp/webui-user-backup-{int(time.time())}.db'")
  cur = con.cursor()
  for uid, s in cur.execute('SELECT id, settings FROM user').fetchall():
      d = json.loads(s) if isinstance(s, str) and s else (s or {})
      ui = d.setdefault('ui', {})
      tts = ui.setdefault('audio', {}).setdefault('tts', {})
      tts['voice'] = tts['defaultVoice'] = 'ono_anna'
      tts['engine'] = 'openai'
      ui['responseAutoPlayback'] = True
      cur.execute('UPDATE user SET settings=? WHERE id=?',
                  (json.dumps(d, ensure_ascii=False), uid))
  con.commit()
  PY
  ```

  После патча обязательно сделать `Ctrl+Shift+R` во вкладке OpenWebUI —
  UI кеширует `user.settings` в `localStorage` и без жёсткого reload
  продолжит слать старый voice.

- **`responseAutoPlayback=false` по умолчанию → ответы LLM молчат.**
  Без Voice Mode и без ручного клика на «🔊 Read aloud» под каждым
  сообщением TTS вообще не вызывается. Симптом: в логах LiteLLM **ноль**
  `POST /v1/audio/speech`, при том что Admin Panel настроен и smoke-тесты
  через `curl` работают. Флаг хранится в `user.settings.ui.responseAutoPlayback`
  per-user (UI: `User Settings → Interface → Auto-playback response`).
  Bulk-enable включён в патч выше.

- **Snap-Chromium и аудио на DGX Spark.** Chromium-snap играет через
  `pipewire-pulse` (`/run/user/$UID/pulse/native`). Если этот сокет
  отсутствует или sink M28U-HDMI на mute — TTS вернёт 200 OK, JS-плеер
  скачает blob, но колонки промолчат. Быстрая проверка минуя браузер:

  ```bash
  # должно сыграть «Проверка звука» из M28U
  curl -sS -m 30 http://localhost:4000/v1/audio/speech \
      -H "Authorization: Bearer sk-local" -H 'Content-Type: application/json' \
      -d '{"model":"tts-ru-default","input":"Проверка звука","voice":"ono_anna"}' \
      -o /tmp/probe.wav
  XDG_RUNTIME_DIR=/run/user/$(id -u) pw-cat --playback /tmp/probe.wav
  ```

  Если `pw-cat` слышно, а в браузере нет — правый клик на табе →
  «Unmute site», или **F12 → Console** → ищи `Autoplay was prevented`.

### Default flow

`OpenWebUI` calls `LiteLLM` for both STT and TTS:

- STT — `POST /v1/audio/transcriptions` с моделью `stt-whisper-cuda-turbo`.
  LiteLLM проксирует в LocalAI → `whisper-large-turbo-q5_0` через
  CUDA-плагин `cuda13-nvidia-l4t-arm64-whisper`. На коротких фразах
  cold-start ≈ 4 с, прогретая модель — sub-second.
- TTS — `POST /v1/audio/speech` с моделью `tts-ru-default`.
  LiteLLM проксирует в LocalAI → `qwen3-tts-1.7b-custom-voice` через
  плагин `cuda13-nvidia-l4t-arm64-qwen-tts`. Voice — встроенный
  CustomVoice; clone через `parameters.audio_path` в YAML модели.

`OpenWebUI` хранит выбор STT/TTS в БД (`openwebui-data-volume`) благодаря
`OPENWEBUI_ENABLE_PERSISTENT_CONFIG=true`. Менять можно в:

- Admin defaults: `Admin Panel → Settings → Audio`
- Per-user voice: `User Settings → Audio → TTS Voice`
- Per-model voice: `Workspace → Models → Edit → TTS Voice`

**Важно**: при изменении дефолтов в [`.env.openwebui`](../.env.openwebui)
существующий volume их **не подхватит** — нужно либо править Admin Panel
руками, либо сначала `docker compose -f compose.openwebui.yml down -v`
(потеряются чаты и пользовательские настройки UI).

Алиасы выставлены в [`docker/litellm/config.yaml`](../docker/litellm/config.yaml).

### Bootstrap backends

Все аудио-плагины LocalAI ставятся одним запуском
[`localai-start.sh`](../localai-start.sh):

```bash
bash localai-start.sh
# → доп. блок 5.5 "Bootstrap audio backends":
#   ставит whisper / qwen-tts / faster-qwen3-tts / fish-speech / piper / silero-vad
#   через POST /backends/apply, polls /backends/jobs/<uuid> до processed:true.
#   Идемпотентно: уже стоящие пропускаются. Жирные pull'ы (~4 ГБ каждый
#   GPU-backend) занимают ~5–10 мин на первый запуск; всего ~25–30 мин.
```

Состояние backends лежит в `localai-models-volume`. После
`docker compose -f compose.localai.yml down -v` оно пропадёт — но
повторный запуск `localai-start.sh` поставит всё заново.

Флаг для быстрых итераций без re-pull:
```bash
LOCALAI_SKIP_BACKENDS=1 bash localai-start.sh
```

### VAD (Voice Activity Detection)

`OpenWebUI` использует **браузерный** VAD (MediaRecorder + порог тишины),
а не серверный — поэтому Silero, поднятый в LocalAI, к нему не подключается.
Чтобы запись по микрофону работала предсказуемо, в
[`Admin Panel → Settings → Audio → Voice Activity Detection`](http://localhost:3000/admin/settings)
нужно выставить:

| Параметр              | Значение |
| --------------------- | -------- |
| Min Speech Duration   | 250 ms   |
| Min Silence Duration  | 500 ms   |
| Speech Threshold      | 0.5      |
| Energy Threshold      | 0.012    |

Эти значения сохраняются в `openwebui-data-volume`. На свежем volume
их можно засеять через `.env.openwebui` (ключи
`OPENWEBUI_AUDIO_VAD_*`), но после первого запуска UI любые правки в
Admin Panel выигрывают и переписывают БД.

`vad-silero` остаётся в LocalAI как `/v1/vad`-endpoint для будущих
realtime-клиентов и для smoke-теста — `LiteLLM` его не проксирует, так
как `/audio/vad` не входит в OpenAI-контракт.

### Browser fallback

Если `LiteLLM` не может достучаться до speaches / openedai-speech /
kokoro, `OpenWebUI` остаётся пригодным для чата. Включите fallback в
`User Settings → Audio`:

- STT Engine: `Web API`
- TTS Engine: `Web API` или `Browser Kokoro`

`stack-start.sh` и `stack-smoke.sh` отдельно сообщают, какие алиасы
сейчас не отвечают, чтобы было видно, когда нужно переключиться на
браузерный fallback. Deprecated алиасы (`stt-whisper-large-v3-turbo`,
`tts-qwen3-1.7b`, `vad-silero`) остаются доступными в реестре и могут
быть выбраны вручную для совместимости со старыми чатами.

## LiteLLM as the model hub

OpenWebUI is deliberately pointed at `LiteLLM`, not directly at LM Studio,
host-side llama.cpp, Docker llama-server, or any remote provider. That keeps one
shared contract for:

- model discovery
- auth (`sk-local`)
- alias naming
- Phoenix tracing

Согласование дефолтных алиасов с OpenHands и Claw Code и smoke-путь sandbox → LiteLLM: [docs/litellm-clients.md](litellm-clients.md).

The active alias lives in `docker/litellm/config.yaml`:

```yaml
model_list:
  - model_name: qwen3.6-35b-heretic
    litellm_params:
      model: openai/qwen3.6-35b-heretic
      api_base: os.environ/LMSTUDIO_API_BASE
      api_key: os.environ/LMSTUDIO_API_KEY
```

The `LMSTUDIO_API_BASE` variable is the historical name for the host-side
OpenAI-compatible LLM upstream. In the current llama.cpp setup it points to
`http://host.docker.internal:8090/v1` via `.env.llamacpp`.

To proxy other models from other sources through the same LiteLLM endpoint,
extend `model_list` with more aliases:

```yaml
model_list:
  - model_name: qwen3.6-35b-heretic
    litellm_params:
      model: openai/qwen3.6-35b-heretic
      api_base: os.environ/LMSTUDIO_API_BASE
      api_key: os.environ/LMSTUDIO_API_KEY

  - model_name: remote-gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_base: os.environ/REMOTE_OPENAI_API_BASE
      api_key: os.environ/REMOTE_OPENAI_API_KEY

  - model_name: ollama-phi4
    litellm_params:
      model: ollama_chat/phi4
      api_base: os.environ/OLLAMA_API_BASE
```

Once LiteLLM is restarted, OpenWebUI discovers those aliases via
`GET /v1/models`. Any other OpenAI-compatible client on the same gateway
(OpenHands, opencode, Claw Code) will see the same list.

The `litellm` container now passes these optional upstream env vars through from
`compose.phoenix.yml`, so you can keep the credentials in `.env` and only
uncomment the aliases you actually need:

```bash
REMOTE_OPENAI_API_BASE=https://api.openai.com/v1
REMOTE_OPENAI_API_KEY=sk-...
OLLAMA_API_BASE=http://host.docker.internal:11434
```

Phase-1 pattern:

1. Set the upstream env vars in `.env`.
2. Uncomment or add the matching alias blocks in `docker/litellm/config.yaml`.
3. Restart `litellm` with `bash ./stack-start.sh` (or `docker compose -f compose.phoenix.yml up -d litellm`).
4. Re-open OpenWebUI and confirm the new aliases appear in the model picker.

## Validation checklist

`stack-smoke.sh` covers:

- direct backend readiness (`LM Studio` or `llama-server`)
- `LiteLLM /v1/models`
- `LiteLLM /v1/chat/completions`
- `LiteLLM /v1/audio/speech`
- `LiteLLM /v1/audio/transcriptions`
- `Phoenix` UI reachability
- `Phoenix` REST span discovery for project `qwen3.6-heretic` after a LiteLLM chat
- `LocalAI /v1/models`
- direct `LocalAI` registry checks for `stt-whisper-large-v3-turbo`,
  `tts-qwen3-1.7b`, and `vad-silero`
- `OpenWebUI /health`
- optional authenticated OpenWebUI `/api/models` and `/api/chat/completions`

For trace validation, send a chat from OpenWebUI using
`qwen3.6-35b-heretic`, then inspect the `qwen3.6-heretic` project in Phoenix at
`http://localhost:6006`.
