"""
CorrIA - Stack de autenticación.
Crea el Cognito User Pool con verificación por email y tres grupos de roles.
"""
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_cognito as cognito,
    RemovalPolicy,
)
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Cognito User Pool ───────────────────────────────────────────────
        self.user_pool = cognito.UserPool(
            self,
            "CorriaUserPool",
            user_pool_name="corria-users",
            self_sign_up_enabled=False,      # solo admin puede crear usuarios
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                given_name=cognito.StandardAttribute(required=False, mutable=True),
                family_name=cognito.StandardAttribute(required=False, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ── App Client (sin secret para uso desde frontend SPA) ─────────────
        self.user_pool_client = self.user_pool.add_client(
            "CorriaWebClient",
            user_pool_client_name="corria-web-client",
            auth_flows=cognito.AuthFlow(
                admin_user_password=True,  # admin-initiate-auth desde CLI/backend
                user_password=True,        # login directo desde frontend React
                user_srp=True,             # flujo seguro usado por Amplify/Cognito SDK
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(implicit_code_grant=True),
                scopes=[cognito.OAuthScope.EMAIL, cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE],
            ),
            generate_secret=False,  # SPAs no pueden guardar secrets de forma segura
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.days(30),
        )

        # ── Grupos de roles ─────────────────────────────────────────────────
        cognito.CfnUserPoolGroup(
            self,
            "GrupoAdmin",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="admin",
            description="Administradores con acceso total",
            precedence=1,
        )

        cognito.CfnUserPoolGroup(
            self,
            "GrupoTecnico",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="tecnico",
            description="Técnicos que realizan mediciones en campo",
            precedence=2,
        )

        cognito.CfnUserPoolGroup(
            self,
            "GrupoCliente",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="cliente",
            description="Clientes con acceso de solo lectura",
            precedence=3,
        )

        # ── Outputs para el frontend ────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID — usar como VITE_COGNITO_USER_POOL_ID",
        )
        cdk.CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito App Client ID — usar como VITE_COGNITO_CLIENT_ID",
        )
