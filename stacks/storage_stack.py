"""
CorrIA - Stack de almacenamiento.
Crea el bucket S3 para imágenes y las tres tablas DynamoDB.
"""
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    RemovalPolicy,
    Duration,
)
from constructs import Construct


class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── S3: bucket de imágenes ──────────────────────────────────────────
        self.bucket = s3.Bucket(
            self,
            "CorriaImagesBucket",
            bucket_name=f"corria-images-{self.account}",
            removal_policy=RemovalPolicy.RETAIN,  # no borrar al hacer destroy
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.POST,
                    ],
                    allowed_origins=[
                        "https://corpacero.vercel.app",
                        "http://localhost:5173",
                    ],
                    allowed_headers=["*"],
                    max_age=3600,
                )
            ],
            lifecycle_rules=[
                # Imágenes raw pasan a Glacier después de 90 días
                s3.LifecycleRule(
                    id="ArchivarImagenesRaw",
                    prefix="raw/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90),
                        )
                    ],
                )
            ],
        )

        cdk.CfnOutput(self, "BucketName", value=self.bucket.bucket_name)

        # ── DynamoDB: puntos de medición ────────────────────────────────────
        self.tabla_puntos = dynamodb.Table(
            self,
            "CorriaPuntos",
            table_name="corria-puntos",
            partition_key=dynamodb.Attribute(
                name="id_punto",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,  # free tier
            removal_policy=RemovalPolicy.RETAIN,
        )

        # GSI para deduplicar puntos por clave lógica empresa+sede+ciudad
        self.tabla_puntos.add_global_secondary_index(
            index_name="ClaveLogicaIndex",
            partition_key=dynamodb.Attribute(
                name="clave_logica",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── DynamoDB: mediciones con historial ──────────────────────────────
        self.tabla_mediciones = dynamodb.Table(
            self,
            "CorriaMediciones",
            table_name="corria-mediciones",
            partition_key=dynamodb.Attribute(
                name="id_punto",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # GSI para GET /mediciones/recientes — todas las mediciones por timestamp desc
        # Usa tipo_registro="medicion" como PK constante (patrón single-table lookup)
        self.tabla_mediciones.add_global_secondary_index(
            index_name="tipo-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="tipo_registro",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI para buscar mediciones por nivel de corrosión
        self.tabla_mediciones.add_global_secondary_index(
            index_name="nivel-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="nivel_corrosion",
                type=dynamodb.AttributeType.NUMBER,
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── DynamoDB: usuarios ──────────────────────────────────────────────
        self.tabla_usuarios = dynamodb.Table(
            self,
            "CorriaUsuarios",
            table_name="corria-usuarios",
            partition_key=dynamodb.Attribute(
                name="id_usuario",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # GSI para buscar usuarios por email
        self.tabla_usuarios.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(
                name="email",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
