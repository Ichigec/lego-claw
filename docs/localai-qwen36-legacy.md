# LocalAI legacy `qwen3.6`

`qwen3.6-35b-heretic` возвращён в `LocalAI` как ручной legacy-профиль.

По умолчанию основной маршрут не меняется:

- `LiteLLM -> LM Studio`
- `LocalAI` остаётся для `ASR/TTS/VAD`

## Включить вручную

```bash
bash ./localai-qwen36-start.sh
```

Скрипт перезапустит `LocalAI` с дополнительным overlay-конфигом и проверит, что
`qwen3.6-35b-heretic` появился в `http://localhost:8180/v1/models`.

## Сохранить при полном bootstrap

Если нужно поднять весь стек и не удалять legacy-модель из `LocalAI`, запускайте:

```bash
LOCALAI_ENABLE_QWEN36=1 bash ./stack-start.sh
LOCALAI_ENABLE_QWEN36=1 bash ./stack-smoke.sh
```

Без `LOCALAI_ENABLE_QWEN36=1` поведение остаётся прежним: `stack-start.sh`
очищает `qwen3.6-35b-heretic` из реестра `LocalAI`.
