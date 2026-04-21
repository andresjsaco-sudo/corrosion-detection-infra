"""
CorrIA - Stack de API Gateway.
Define los endpoints REST con autorizador Cognito y CORS habilitado.
"""
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_apigateway as apigw,
    aws_cognito as cognito,
)
from constructs import Construct

from stacks.compute_stack import ComputeStack
from stacks.auth_stack import AuthStack

# Orígenes permitidos para CORS
CORS_ORIGINS = [
    "https://corpacero.vercel.app",
    "http://localhost:5173",
]


def _cors_options() -> apigw.CorsOptions:
    return apigw.CorsOptions(
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Amz-Date",
            "X-Api-Key",
            "X-Amz-Security-Token",
        ],
        max_age=cdk.Duration.hours(1),
    )


class ApiStack(Stack):
    def __init__(
        self,
        scope: "Construct",
        construct_id: str,
        compute_stack: ComputeStack,
        auth_stack: AuthStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── API Gateway REST ────────────────────────────────────────────────
        self.api = apigw.RestApi(
            self,
            "CorriaApi",
            rest_api_name="corria-api",
            description="API REST de CorrIA para monitoreo de corrosión industrial",
            default_cors_preflight_options=_cors_options(),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=100,
                throttling_burst_limit=200,
            ),
        )

        # ── Autorizador Cognito JWT ─────────────────────────────────────────
        autorizador = apigw.CognitoUserPoolsAuthorizer(
            self,
            "CorriaAutorizador",
            cognito_user_pools=[auth_stack.user_pool],
            authorizer_name="corria-cognito-auth",
            identity_source="method.request.header.Authorization",
        )

        opciones_auth = {
            "authorizer": autorizador,
            "authorization_type": apigw.AuthorizationType.COGNITO,
        }

        # ── Integraciones Lambda ────────────────────────────────────────────
        integ_inference = apigw.LambdaIntegration(compute_stack.fn_inference)
        integ_puntos = apigw.LambdaIntegration(compute_stack.fn_api_puntos)
        integ_mediciones = apigw.LambdaIntegration(compute_stack.fn_api_mediciones)
        integ_alertas = apigw.LambdaIntegration(compute_stack.fn_api_alertas)
        integ_usuarios = apigw.LambdaIntegration(compute_stack.fn_api_usuarios)
        integ_reportes = apigw.LambdaIntegration(compute_stack.fn_api_reportes)

        # ── POST /medicion → inferencia + guardar resultado ─────────────────
        medicion = self.api.root.add_resource("medicion")
        medicion.add_method("POST", integ_inference, **opciones_auth)

        # ── GET /mediciones/recientes + GET /mediciones/{id_punto} ────────────
        # "recientes" debe declararse ANTES de {id_punto} para que el literal
        # tome precedencia sobre el path parameter en API Gateway.
        mediciones = self.api.root.add_resource("mediciones")
        mediciones_recientes = mediciones.add_resource("recientes")
        mediciones_recientes.add_method("GET", integ_mediciones, **opciones_auth)
        mediciones_punto = mediciones.add_resource("{id_punto}")
        mediciones_punto.add_method("GET", integ_mediciones, **opciones_auth)

        # ── GET /puntos + POST /puntos ──────────────────────────────────────
        puntos = self.api.root.add_resource("puntos")
        puntos.add_method("GET", integ_puntos, **opciones_auth)
        puntos.add_method("POST", integ_puntos, **opciones_auth)

        # ── GET /puntos/buscar?empresa=&ciudad= ─────────────────────────────
        # "buscar" debe declararse ANTES de {id_punto} por la misma razón.
        puntos_buscar = puntos.add_resource("buscar")
        puntos_buscar.add_method("GET", integ_puntos, **opciones_auth)

        # ── GET|PUT|DELETE /puntos/{id_punto} ───────────────────────────────
        punto_id = puntos.add_resource("{id_punto}")
        punto_id.add_method("GET", integ_puntos, **opciones_auth)
        punto_id.add_method("PUT", integ_puntos, **opciones_auth)
        punto_id.add_method("DELETE", integ_puntos, **opciones_auth)

        # ── GET /alertas → mediciones críticas recientes ────────────────────
        alertas = self.api.root.add_resource("alertas")
        alertas.add_method("GET", integ_alertas, **opciones_auth)

        # ── /usuarios — gestión de usuarios ────────────────────────────────
        # /usuarios/me debe declararse ANTES de /usuarios/{id_usuario} para
        # evitar que el literal "me" sea interpretado como path parameter.
        usuarios = self.api.root.add_resource("usuarios")
        usuarios.add_method("POST", integ_usuarios, **opciones_auth)
        usuarios.add_method("GET", integ_usuarios, **opciones_auth)

        usuarios_me = usuarios.add_resource("me")
        usuarios_me.add_method("GET", integ_usuarios, **opciones_auth)
        usuarios_me.add_method("PUT", integ_usuarios, **opciones_auth)

        usuario_id = usuarios.add_resource("{id_usuario}")
        usuario_id.add_method("PUT", integ_usuarios, **opciones_auth)
        usuario_id.add_method("DELETE", integ_usuarios, **opciones_auth)

        # ── /reportes — reportes por planta y global ────────────────────────
        reportes = self.api.root.add_resource("reportes")

        reportes_global = reportes.add_resource("global")
        reportes_global.add_method("GET", integ_reportes, **opciones_auth)

        # "planta" debe declararse antes por claridad — no hay conflicto aquí
        reportes_planta = reportes.add_resource("planta")
        reportes_planta_id = reportes_planta.add_resource("{id_punto}")
        reportes_planta_id.add_method("GET", integ_reportes, **opciones_auth)

        # ── Outputs ─────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=self.api.url,
            description="URL base de la API — usar como VITE_API_URL",
        )
