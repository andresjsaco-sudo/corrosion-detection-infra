# Corrosion detection: AWS infrastructure

Serverless backend for an IoT system that detects early corrosion on steel plates. Field cameras and Raspberry Pi nodes upload images; a YOLOv8 segmentation model running on ONNX Runtime in AWS Lambda classifies the corrosion, and results are served through a Cognito-secured REST API to a React dashboard. All infrastructure is defined as code with AWS CDK in Python.

Frontend: [corrosion-detection-dashboard](https://github.com/andresjsaco-sudo/corrosion-detection-dashboard)

## Arquitectura

```
  Frontend (Vercel)          AWS Cloud (us-east-1)
  ─────────────────          ──────────────────────────────────────────────────

  React + Vite   ────────►  API Gateway REST (corria-api)
  your-frontend.vercel.app       │
                                │  Cognito JWT Authorizer
                                │  (corria-users)
                                │
              ┌─────────────────┼──────────────────────┐
              │                 │                      │
              ▼                 ▼                      ▼
    Lambda: corria-      Lambda: corria-      Lambda: corria-
    api-puntos           api-mediciones       api-alertas
         │                    │                    │
         │                    │                    │
         ▼                    ▼                    ▼
  DynamoDB:            DynamoDB:            DynamoDB:
  corria-puntos        corria-mediciones    corria-mediciones
                       (GSI: nivel)         (GSI: nivel)

  POST /medicion ──────► Lambda: corria-inference
                              │  (YOLOv8-seg ONNX, 1024 MB RAM)
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
             S3: corria-images     DynamoDB:
             raw/  resultados/     corria-mediciones

  POST /usuarios ─────► Lambda: corria-api-usuarios
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
             Cognito IDP           DynamoDB:
             (crear usuarios)      corria-usuarios

  Raspberry Pi ──────► API Gateway → corria-inference
  (nodos fijos)         (imagen + inferencia_local)
```

## Prerequisitos

- Python 3.11+
- Node 18+
- AWS CLI configurado (`aws configure`)
- AWS CDK 2.x (`npm install -g aws-cdk`)
- Docker Desktop (para bundling de Lambda con dependencias nativas)

## Primeros pasos

```bash
# Activar virtualenv (cada vez que abras una terminal nueva)
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Verificar configuración AWS
aws sts get-caller-identity
```

## Despliegue

```bash
# Validar que todo compila sin errores
cdk synth

# Desplegar todos los stacks en orden
cdk deploy --all

# Desplegar solo un stack específico
cdk deploy CorriaStorageStack
```

Define `FRONTEND_ORIGIN` con la URL de tu frontend para CORS antes de desplegar. Después del deploy, CDK imprimirá los outputs:

```
CorriaApiStack.ApiUrl          = https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/prod/
CorriaAuthStack.UserPoolId     = us-east-1_xxxxxxxxx
CorriaAuthStack.UserPoolClientId = xxxxxxxxxxxxxxxxxxxxxxxxxx
CorriaStorageStack.BucketName  = corria-images-<account-id>
```

Usa estos valores en el archivo `.env.local` del frontend.

## Conectar el frontend React

Copia `.env.example` al repo del frontend como `.env.local`:

```bash
# En el repo del frontend (corrosion-detection-dashboard)
cp /ruta/a/corria-infra/.env.example .env.local
# Edita .env.local con los outputs del deploy
```

Variables necesarias:

```
VITE_API_URL=https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/prod
VITE_COGNITO_USER_POOL_ID=us-east-1_xxxxxxxxx
VITE_COGNITO_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
VITE_AWS_REGION=us-east-1
```

## Reemplazar el modelo ONNX (paso crítico antes de producción)

El archivo `lambda/inference/model/yolov8n-seg.onnx` es un **placeholder vacío**.
Debes reemplazarlo con tu modelo entrenado antes de hacer deploy a producción.

### Entrenar y exportar en Google Colab

```python
# Instalar dependencias
!pip install ultralytics roboflow

# Descargar dataset desde Roboflow
from roboflow import Roboflow
rf = Roboflow(api_key="TU_API_KEY")
project = rf.workspace("TU_WORKSPACE").project("corrosion-segmentation")
dataset = project.version(1).download("yolov8")

# Entrenar YOLOv8-seg
from ultralytics import YOLO
model = YOLO("yolov8n-seg.pt")
model.train(
    data=f"{dataset.location}/data.yaml",
    epochs=100,
    imgsz=640,
    batch=16,
    project="corria_runs",
    name="yolov8n-seg-corrosion",
)

# Exportar a ONNX (compatible con onnxruntime en Lambda)
model.export(
    format="onnx",
    imgsz=640,
    opset=12,        # máxima compatibilidad con onnxruntime
    simplify=True,
)

# El archivo exportado estará en:
# corria_runs/yolov8n-seg-corrosion/weights/best.onnx

# Descargar desde Colab
from google.colab import files
files.download("corria_runs/yolov8n-seg-corrosion/weights/best.onnx")
```

Luego copia el modelo exportado:

```bash
cp ~/Downloads/best.onnx lambda/inference/model/yolov8n-seg.onnx
cdk deploy CorriaComputeStack  # redespliega solo la Lambda
```

## Destruir la infraestructura

```bash
cdk destroy --all
```

**Nota:** S3 y DynamoDB tienen `RemovalPolicy.RETAIN`: no se borran automáticamente.
Para eliminarlos manualmente usa la consola de AWS o AWS CLI.

## Costos estimados (Free Tier)

| Servicio | Free Tier | Uso estimado CorrIA |
|---|---|---|
| Lambda | 1M req/mes + 400K GB-s | ~10K req/mes |
| API Gateway | 1M req/mes (primer año) | ~10K req/mes |
| DynamoDB | 25 GB + 25 WCU/RCU | < 1 GB |
| S3 | 5 GB + 20K GET + 2K PUT | < 1 GB |
| Cognito | 50K MAU gratis | < 50 usuarios |

**Costo estimado en producción ligera: < $5 USD/mes**

## Estructura del proyecto

```
corria-infra/
├── app.py                    # CDK entry point
├── cdk.json                  # Configuración CDK
├── requirements.txt          # Dependencias Python
├── .env.example              # Variables de entorno para el frontend
├── stacks/
│   ├── storage_stack.py      # S3 + DynamoDB
│   ├── auth_stack.py         # Cognito
│   ├── compute_stack.py      # Lambda functions
│   └── api_stack.py          # API Gateway REST
├── lambda/
│   ├── inference/
│   │   ├── handler.py        # YOLOv8 ONNX + S3 + DynamoDB
│   │   ├── requirements.txt  # onnxruntime, Pillow, numpy
│   │   └── model/
│   │       └── yolov8n-seg.onnx  ⚠️ PLACEHOLDER: reemplazar
│   ├── api_puntos/handler.py
│   ├── api_mediciones/handler.py
│   ├── api_alertas/handler.py
│   └── api_usuarios/handler.py
└── tests/
    └── test_inference_local.py
```
