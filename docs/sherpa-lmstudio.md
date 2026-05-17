# Sherpa-ONNX + LM Studio

Локально установлено:

- `Sherpa-ONNX` в `.venv-sherpa`
- русская ASR-модель в `models/sherpa-onnx-zipformer-ru-2024-09-18`
- bridge-скрипт в `scripts/sherpa-lmstudio`

Важно: это не нативный плагин внутри `LM Studio`. Связка работает как внешний мост:

`wav -> Sherpa-ONNX -> LM Studio /v1/responses`

## Быстрый запуск

1. Убедитесь, что `LM Studio` запущен и локальный сервер доступен на `http://localhost:1234`.
2. Загрузите LLM-модель в память в `LM Studio`.
3. Запустите:

```bash
# Подставьте любой WAV с русской речью (16 kHz, mono) на месте input.wav.
./scripts/sherpa-lmstudio input.wav
```

Скрипт сам попытается взять первую загруженную модель из `lms ps --json`.

## Полезные варианты

Явно указать модель:

```bash
./scripts/sherpa-lmstudio \
  --llm-model tvall43-qwen3.6-35b-a3b-heretic \
  input.wav
```

Только расшифровка без вызова `LM Studio`:

```bash
./scripts/sherpa-lmstudio --transcript-only input.wav
```

JSON-вывод:

```bash
./scripts/sherpa-lmstudio --json input.wav
```

Свой prompt-шаблон:

```bash
./scripts/sherpa-lmstudio \
  --prompt-template "Пользователь сказал: {transcript}\n\nСделай краткое резюме." \
  input.wav
```

## Ограничения

- вход сейчас ожидается как `mono 16-bit PCM WAV`
- это внешняя интеграция рядом с `LM Studio`, а не встроенный voice mode
- для микрофона удобнее потом добавить отдельный потоковый скрипт

## Что уже проверено

- `Sherpa-ONNX` установлен и работает на `aarch64`
- русская модель успешно расшифровывает `input.wav` (любой WAV с русской речью)
- `LM Studio` сервер отвечает на `http://localhost:1234/v1`
