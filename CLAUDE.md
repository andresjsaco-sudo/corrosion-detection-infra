# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**CorrIA** is an AWS-native corrosion monitoring system for industrial infrastructure. It uses YOLOv8-seg ONNX inference to detect and classify corrosion in images, exposing results through a Cognito-secured REST API. Infrastructure is fully defined in AWS CDK (Python).

> **Critical**: `lambda/inference/model/yolov8n-seg.onnx` is a placeholder (empty file). Production deployment requires replacing it with a trained model.

## Commands

```bash
# Activate virtual environment (required before any cdk/python commands)
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Validate CDK synthesis (no deployment)
cdk synth

# Deploy all stacks in dependency order
cdk deploy --all

# Deploy a specific stack
cdk deploy CorriaStorageStack   # or AuthStack, ComputeStack, ApiStack

# Build Lambda Layer for ML dependencies (~80MB, Docker-based)
scripts/build_inference_layer.sh

# Run inference tests (mocks S3, DynamoDB, ONNX)
python tests/test_inference_local.py

# Destroy all infrastructure
cdk destroy --all
# NOTE: S3 bucket and DynamoDB tables use RemovalPolicy.RETAIN — delete manually after destroy
```

## Architecture

The CDK app (`app.py`) deploys four stacks in this dependency order:

```
StorageStack → AuthStack → ComputeStack → ApiStack
```

### Stack Responsibilities

**StorageStack** (`stacks/storage_stack.py`)
- S3 bucket `corria-images-{account_id}`: raw uploads and processed results, with 90-day Glacier lifecycle on `raw/`
- Three DynamoDB tables (PAY_PER_REQUEST):
  - `corria-puntos` — measurement points (company + location); GSI for deduplication by `clave_logica`
  - `corria-mediciones` — per-measurement records; GSIs for querying by timestamp and corrosion level
  - `corria-usuarios` — user profiles; GSI by email

**AuthStack** (`stacks/auth_stack.py`)
- Cognito User Pool with email+password sign-in; self sign-up disabled
- Three role groups: `admin` (precedence 1), `tecnico` (2), `cliente` (3)
- Token TTL: 1h access/ID, 30d refresh

**ComputeStack** (`stacks/compute_stack.py`)
- Six Lambda functions wired to StorageStack and AuthStack resources via environment variables
- ML inference function: 1024 MB RAM, 30s timeout, uses a Lambda Layer built by `scripts/build_inference_layer.sh`
- Report function: 512 MB RAM

| Function | Responsibility |
|---|---|
| `corria-inference` | YOLOv8-seg ONNX inference; writes images to S3, measurements to DynamoDB |
| `corria-api-puntos` | CRUD for measurement points; admin-gated writes |
| `corria-api-mediciones` | Read measurement history; returns S3 presigned URLs |
| `corria-api-alertas` | Returns measurements with corrosion level ≥ 2 |
| `corria-api-usuarios` | User management; integrates Cognito for pool operations |
| `corria-api-reportes` | Aggregated corrosion reports (global and per-location) |

**ApiStack** (`stacks/api_stack.py`)
- REST API Gateway (`corria-api`, stage `prod`): 100 RPS throttle, 200 burst
- Cognito JWT authorizer on all routes
- CORS for `corpacero.vercel.app` and `localhost:5173`
- Path-specific routes take precedence over `{id}` parameters (e.g., `/puntos/buscar` registered before `/puntos/{id_punto}`)

### Request Flow

```
Frontend (Vercel) → API Gateway (JWT) → Lambda → DynamoDB / S3
```

### Corrosion Levels

| Level | Description | Threshold |
|---|---|---|
| 0 | None | 0% |
| 1 | Light | 1–10% |
| 2 | Moderate | 10–30% |
| 3 | Severe | >30% |

## Key Configuration

- **AWS Account**: `311208284976`, **Region**: `us-east-1`
- **cdk.json**: entry point is `python3 app.py`; includes 47 CDK feature flags
- **`.env.example`**: template for frontend environment variables (API URL, Cognito pool/client IDs, region)
- **`.gitignore`**: excludes `.venv/`, `cdk.out/`, `*.onnx`, `lambda/layer_inference/`

## Lambda Layer

The inference Lambda Layer (`lambda/layer_inference/python/`) contains `onnxruntime`, `Pillow`, and `numpy` compiled for Amazon Linux. It is built by `scripts/build_inference_layer.sh` (Docker-preferred, falls back to local pip) and is excluded from git. Rebuild it after changing `lambda/inference/requirements.txt`.
