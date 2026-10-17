#!/usr/bin/env python3
"""
CorrIA - Punto de entrada de la aplicación CDK.
Orquesta todos los stacks de infraestructura en orden de dependencia.
"""
import os

import aws_cdk as cdk

from stacks.storage_stack import StorageStack
from stacks.auth_stack import AuthStack
from stacks.compute_stack import ComputeStack
from stacks.api_stack import ApiStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# 1. Almacenamiento: S3 + DynamoDB
storage = StorageStack(app, "CorriaStorageStack", env=env)

# 2. Autenticación: Cognito User Pool + grupos
auth = AuthStack(app, "CorriaAuthStack", env=env)

# 3. Cómputo: Lambda functions con acceso a storage y auth
compute = ComputeStack(
    app,
    "CorriaComputeStack",
    storage_stack=storage,
    auth_stack=auth,
    env=env,
)

# 4. API Gateway REST con CORS y autorizador Cognito
api = ApiStack(
    app,
    "CorriaApiStack",
    compute_stack=compute,
    auth_stack=auth,
    env=env,
)

app.synth()
