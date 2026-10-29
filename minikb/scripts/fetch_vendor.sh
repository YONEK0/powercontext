#!/usr/bin/env bash
# 拉取前端 vendor 资源（transformers.js 3.8.1 + ONNX Runtime Web wasm）。
# 仓库里不存这两个大文件，保证 clone 体积小；首次运行前执行一次。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p public/vendor/ort
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "→ @huggingface/transformers 3.8.1"
curl -sSL --retry 3 https://registry.npmmirror.com/@huggingface/transformers/-/transformers-3.8.1.tgz -o "$TMP/tf.tgz"
tar xzf "$TMP/tf.tgz" -C "$TMP"
cp "$TMP/package/dist/transformers.min.js" public/vendor/

echo "→ onnxruntime-web 1.22.0-dev（jsep wasm）"
curl -sSL --retry 3 "https://registry.npmmirror.com/onnxruntime-web/-/onnxruntime-web-1.22.0-dev.20250409-89f8206ba4.tgz" -o "$TMP/ort.tgz"
tar xzf "$TMP/ort.tgz" -C "$TMP"
cp "$TMP/package/dist/ort-wasm-simd-threaded.jsep.mjs" public/vendor/ort/
cp "$TMP/package/dist/ort-wasm-simd-threaded.jsep.wasm" public/vendor/ort/

ls -lh public/vendor public/vendor/ort
