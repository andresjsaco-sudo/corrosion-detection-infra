#!/bin/bash
# Construye el Lambda Layer con las dependencias de inferencia ML.
set -e

LAYER_DIR="lambda/layer_inference/python"
REQS_FILE="lambda/inference/requirements.txt"

echo "==> Limpiando directorio del layer..."
rm -rf "$LAYER_DIR"
mkdir -p "$LAYER_DIR"

if command -v docker &> /dev/null && docker info &> /dev/null; then
    echo "==> Construyendo layer con Docker (entorno Linux compatible con Lambda)..."
    docker run --rm \
        --entrypoint /bin/bash \
        -v "$(pwd)/$LAYER_DIR":/layer \
        -v "$(pwd)/$REQS_FILE":/requirements.txt \
        public.ecr.aws/lambda/python:3.11 \
        -c "pip install -r /requirements.txt -t /layer --no-cache-dir"
else
    echo "==> Docker no disponible. Instalando deps con pip local..."
    pip install \
        -r "$REQS_FILE" \
        -t "$LAYER_DIR" \
        --no-cache-dir \
        --platform manylinux2014_x86_64 \
        --only-binary=:all: \
        --python-version 3.11
fi

echo ""
echo "==> Layer construido en $LAYER_DIR"
echo "    Tamaño: $(du -sh $LAYER_DIR | cut -f1)"
echo ""
echo "Ahora puedes ejecutar: cdk deploy CorriaComputeStack"
