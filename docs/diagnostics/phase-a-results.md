# Phase A. Результаты параметрической диагностики

Дата: 2026-04-26
Бинарь: `${HOME}/src/llama.cpp/build/bin/llama-server`, версия `b8933 (dcad77cc3)`,
собран `GNU 12.4.0 for Linux aarch64`, `CMAKE_CUDA_ARCHITECTURES=121` (только sm_121).
GPU: `NVIDIA GB10`, compute cap `12.1`, driver `580.142`, VRAM 124 610 MiB.
Модель: `Qwen3.6-35B-A3B-heretic-bf16.gguf` (69 GB) + mmproj F32.

Каждый прогон запускался с `--no-warmup`, `--threads 20`,
`--batch-size 64 --ubatch-size 64 --parallel 1`, `--ctx-size 4096..8192` и
без `--cont-batching`. Полные логи лежат рядом (`A*.log`).

## Сводка прогонов

| ID  | n_gpu_layers | flash-attn | env-флаги                                                              | Результат |
| --- | ------------ | ---------- | ---------------------------------------------------------------------- | --------- |
| A1  | 40 (full)    | off        | —                                                                      | crash     |
| A2  | 40 (full)    | off        | `GGML_CUDA_DISABLE_GRAPHS=1 GGML_CUDA_NO_PEER_COPY=1 GGML_CUDA_FORCE_CUBLAS=1` | crash     |
| A3  | 40 (full)    | on         | —                                                                      | crash     |
| A4  | 0 (CPU only) | off        | —                                                                      | OK, чат отвечает (~1.5 tok/s) |
| A5  | 1 (output only) | off     | —                                                                      | OK, чат отвечает (~1.7 tok/s) |
| A6  | 2 (output + 1 transformer block) | off | —                                                          | crash     |

Все «crash» — одинаковый стек:

```
ggml_cuda_compute_forward: SOFT_MAX failed
ggml-cuda.cu:97: CUDA error: invalid argument
  ... in ggml_cuda_compute_forward at ggml-cuda.cu:2962
  -> ggml_backend_cuda_graph_compute
  -> llama_context::process_ubatch
  -> llama_decode
  -> common_context_can_seq_rm  (probe из server_context_impl::load_model)
```

Падает уже на пробном `decode` внутри инициализации слотов
(`srv load_model: initializing slots, n_slots = 1`),
до того как сервер начинает слушать порт. То есть `--no-warmup` не спасает.

## Что это значит

- Граф модели сам по себе исправен: CPU-only (`-ngl 0`) и «output-only»
  (`-ngl 1`, 0 трансформер-блоков на GPU) проходят и отвечают на чат.
- Падение жёстко привязано к **первому же транзформер-блоку, считаемому на GPU**
  (`-ngl 2` уже валится). Параметры `ctx-size`, `batch-size`, `ubatch-size`,
  `parallel`, `flash-attn` и env-флаги `GGML_CUDA_DISABLE_GRAPHS`,
  `GGML_CUDA_NO_PEER_COPY`, `GGML_CUDA_FORCE_CUBLAS` не влияют —
  падение всегда одно и то же, в SOFT_MAX kernel.
- «Безопасной» GPU-конфигурации с реальной полезностью нет:
  `-ngl 0/1` работают, но это эффективно CPU-инференс на 35B bf16 (~1.5 tok/s),
  что для прода непригодно.

## Граница падения

- `n_gpu_layers ≤ 1` (то есть 0 трансформер-блоков на GPU) — **OK**.
- `n_gpu_layers ≥ 2` (>=1 трансформер-блок на GPU) — **crash**, независимо
  от остальных параметров.

## Следующий шаг

Phase A не дала рабочей GPU-конфигурации => переходим к Phase B
(обновить `llama.cpp` до свежего master и пересобрать с
`-DCMAKE_CUDA_ARCHITECTURES="120;121"`, как и предусмотрено планом).

Доп. артефакт для будущего issue в апстриме: точный коммит, на котором ловится
краш — `b8933 (dcad77cc3)` под sm_121-only сборку на GB10 / driver 580.142 / CUDA 13.0.
