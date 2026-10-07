#!/usr/bin/env bash
# Constrói basalt/static/vendor/basalt-editor.min.js a partir de src/editor.js
# Uso: ./construir.sh   (a partir de editor-src/, na raiz do repositório)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d node_modules ]; then
  echo "A instalar dependências (npm install)…"
  npm install
fi

mkdir -p ../basalt/static/vendor

./node_modules/.bin/esbuild src/editor.js \
  --bundle \
  --minify \
  --format=iife \
  --global-name=BasaltEditor \
  --target=es2020 \
  --outfile=../basalt/static/vendor/basalt-editor.min.js

tamanho=$(du -h ../basalt/static/vendor/basalt-editor.min.js | cut -f1)
echo "OK: basalt/static/vendor/basalt-editor.min.js ($tamanho)"
