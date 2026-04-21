---
name: Proyecto CorrIA infraestructura
description: Decisiones de arquitectura AWS CDK para el backend de CorrIA
type: project
---

Infraestructura CDK Python en /Users/andresjsaco/Personal/Proyectos/corria-infra/.

**Stacks desplegados (en orden):**
1. CorriaStorageStack — S3 (corria-images-311208284976) + 3 tablas DynamoDB
2. CorriaAuthStack — Cognito User Pool "corria-users" con grupos admin/tecnico/cliente
3. CorriaComputeStack — 5 Lambdas Python 3.11 + Lambda Layer para ML deps
4. CorriaApiStack — API Gateway REST con autorizador Cognito JWT

**Decisión clave:** Lambda de inferencia usa Layer (lambda/layer_inference/python/) en lugar de Docker bundling. Requiere correr scripts/build_inference_layer.sh antes del deploy.

**Why:** Docker no disponible en el Mac del usuario. Layer con --platform manylinux2014_x86_64 genera binarios compatibles con Lambda Linux.

**How to apply:** Siempre recordar que el layer debe construirse antes de `cdk deploy CorriaComputeStack`. Si el usuario actualiza onnxruntime/Pillow/numpy, debe reconstruir el layer.

**Modelo ONNX:** lambda/inference/model/yolov8n-seg.onnx es placeholder vacío. El real se exporta desde Colab con ultralytics y se reemplaza manualmente.

**CORS:** API Gateway y S3 permiten https://corpacero.vercel.app y http://localhost:5173.
