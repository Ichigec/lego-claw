# llama.cpp (llama-server) для arm64 + CUDA 13, оптимизация под Blackwell (sm_121, DGX Spark).
# Multi-stage сборка: build → runtime. Итоговый образ несёт только llama-server и нужные .so.
#
# Важно: на DGX Spark/Grace ARM64 нужны обычные multi-arch образы `nvidia/cuda`.
# `nvcr.io/nvidia/l4t-cuda:*` — это семейство Jetson/L4T, и тега `13.0.0-*` там нет.
#
# Ключевые флаги:
#   -DGGML_CUDA=ON              — CUDA backend
#   -DLLAMA_CURL=ON             — поддержка HTTP загрузки моделей (нам не критично, но дешёво)
#   -DCMAKE_CUDA_ARCHITECTURES  — sm_121 (Blackwell). Fallback см. README plan'а (120 / native).

FROM nvidia/cuda:13.0.0-devel-ubuntu24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential libcurl4-openssl-dev pkg-config ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth=1 https://github.com/ggml-org/llama.cpp .
ARG CUDA_ARCH=121
RUN cmake -B build \
        -DGGML_CUDA=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target llama-server -j

FROM nvidia/cuda:13.0.0-runtime-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=build /src/build/bin/*.so /usr/local/lib/
RUN ldconfig
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/llama-server"]
