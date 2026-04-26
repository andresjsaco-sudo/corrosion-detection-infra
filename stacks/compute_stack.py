"""
CorrIA - Stack de cómputo.
Define las cinco Lambda functions con sus permisos y variables de entorno.

Estrategia de packaging:
  - Las dependencias ML pesadas (onnxruntime, Pillow, numpy ~80MB) van en un
    Lambda Layer construido con scripts/build_inference_layer.sh.
  - Los handlers van como código plain sin bundling (no requiere Docker).
"""
import os
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    aws_iam as iam,
    Duration,
)
from constructs import Construct

from stacks.storage_stack import StorageStack
from stacks.auth_stack import AuthStack


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        storage_stack: StorageStack,
        auth_stack: AuthStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Variables de entorno compartidas entre todas las Lambdas
        env_comun = {
            "BUCKET_NAME": storage_stack.bucket.bucket_name,
            "TABLA_PUNTOS": storage_stack.tabla_puntos.table_name,
            "TABLA_MEDICIONES": storage_stack.tabla_mediciones.table_name,
            "TABLA_USUARIOS": storage_stack.tabla_usuarios.table_name,
            "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            "REGION": self.region,
        }

        # ── Layer con dependencias ML (onnxruntime, Pillow, numpy) ──────────
        # Construir con: scripts/build_inference_layer.sh
        # El directorio lambda/layer_inference/python/ debe existir.
        layer_inference = _lambda.LayerVersion(
            self,
            "CorriaInferenceLayer",
            layer_version_name="corria-inference-deps",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/layer_inference")
            ),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_11],
            description="onnxruntime + Pillow + numpy para inferencia YOLOv8",
        )

        # ── Lambda de inferencia YOLOv8 ONNX ───────────────────────────────
        # 1024MB de RAM para que la inferencia corra fluido
        self.fn_inference = _lambda.Function(
            self,
            "CorriaInference",
            function_name="corria-inference",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/inference")
            ),
            layers=[layer_inference],
            memory_size=1024,
            timeout=Duration.seconds(30),
            environment=env_comun,
            description="Inferencia YOLOv8-seg ONNX para detección de corrosión",
        )

        # ── Lambda CRUD puntos de medición ──────────────────────────────────
        self.fn_api_puntos = _lambda.Function(
            self,
            "CorriaApiPuntos",
            function_name="corria-api-puntos",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/api_puntos")
            ),
            timeout=Duration.seconds(30),
            environment=env_comun,
            description="CRUD de puntos de medición",
        )

        # ── Lambda CRUD mediciones e historial ──────────────────────────────
        self.fn_api_mediciones = _lambda.Function(
            self,
            "CorriaApiMediciones",
            function_name="corria-api-mediciones",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/api_mediciones")
            ),
            timeout=Duration.seconds(30),
            environment=env_comun,
            description="CRUD de mediciones e historial por punto",
        )

        # ── Lambda alertas (mediciones nivel >= 2) ──────────────────────────
        self.fn_api_alertas = _lambda.Function(
            self,
            "CorriaApiAlertas",
            function_name="corria-api-alertas",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/api_alertas")
            ),
            timeout=Duration.seconds(30),
            environment=env_comun,
            description="Lista mediciones con nivel de corrosión moderada o severa",
        )

        # ── Lambda gestión de usuarios (admin + perfil propio) ──────────────
        self.fn_api_usuarios = _lambda.Function(
            self,
            "CorriaApiUsuarios",
            function_name="corria-api-usuarios",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/api_usuarios")
            ),
            timeout=Duration.seconds(30),
            environment=env_comun,
            description="Gestión de usuarios del sistema y perfil propio",
        )

        # ── Lambda reportes (lectura, todos los roles) ───────────────────────
        self.fn_api_reportes = _lambda.Function(
            self,
            "CorriaApiReportes",
            function_name="corria-api-reportes",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../lambda/api_reportes")
            ),
            memory_size=512,
            timeout=Duration.seconds(30),
            environment=env_comun,
            description="Generación de reportes de corrosión por planta y global",
        )

        # ── Permisos S3 ─────────────────────────────────────────────────────
        storage_stack.bucket.grant_read_write(self.fn_inference)
        storage_stack.bucket.grant_read(self.fn_api_mediciones)
        storage_stack.bucket.grant_read(self.fn_api_reportes)

        # ── Permisos DynamoDB ───────────────────────────────────────────────
        for tabla in [
            storage_stack.tabla_puntos,
            storage_stack.tabla_mediciones,
            storage_stack.tabla_usuarios,
        ]:
            tabla.grant_read_write_data(self.fn_inference)

        storage_stack.tabla_puntos.grant_read_write_data(self.fn_api_puntos)
        storage_stack.tabla_mediciones.grant_read_data(self.fn_api_mediciones)
        storage_stack.tabla_mediciones.grant_read_data(self.fn_api_alertas)
        storage_stack.tabla_usuarios.grant_read_write_data(self.fn_api_usuarios)

        # Reportes solo necesita lectura en puntos y mediciones
        storage_stack.tabla_puntos.grant_read_data(self.fn_api_reportes)
        storage_stack.tabla_mediciones.grant_read_data(self.fn_api_reportes)

        # Permisos de Cognito para fn_api_usuarios
        self.fn_api_usuarios.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminAddUserToGroup",
                    "cognito-idp:AdminRemoveUserFromGroup",
                    "cognito-idp:AdminDisableUser",
                    "cognito-idp:AdminDeleteUser",
                    "cognito-idp:AdminGetUser",
                    "cognito-idp:ListUsers",
                ],
                resources=[auth_stack.user_pool.user_pool_arn],
            )
        )
