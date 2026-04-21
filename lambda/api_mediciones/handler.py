"""
CorrIA - Lambda de mediciones.

Rutas:
  GET /mediciones/{id_punto}    → historial de un punto (cualquier rol)
  GET /mediciones/recientes     → últimas N mediciones de todos los puntos (cualquier rol)
"""
import json
import logging
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLA_MEDICIONES = os.environ["TABLA_MEDICIONES"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
REGION = os.environ["REGION"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
tabla = dynamodb.Table(TABLA_MEDICIONES)
s3 = boto3.client("s3", region_name=REGION)


class DecimalEncoder(json.JSONEncoder):
    """Convierte Decimal a float al serializar la respuesta JSON para el cliente."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _respuesta(codigo: int, cuerpo) -> dict:
    return {
        "statusCode": codigo,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(cuerpo, ensure_ascii=False, cls=DecimalEncoder),
    }


def _agregar_url_imagen(items: list) -> list:
    """Agrega URLs prefirmadas S3 (válidas 1 hora) a cada medición."""
    for item in items:
        if item.get("s3_key_imagen"):
            try:
                item["url_imagen"] = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": BUCKET_NAME, "Key": item["s3_key_imagen"]},
                    ExpiresIn=3600,
                )
            except Exception:
                item["url_imagen"] = None
    return items


def lambda_handler(event: dict, context) -> dict:
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    id_punto = path_params.get("id_punto")

    try:
        # ── GET /mediciones/recientes ────────────────────────────────────────
        if resource == "/mediciones/recientes":
            qp = event.get("queryStringParameters") or {}
            limite = min(int(qp.get("limit", 20)), 100)  # máximo 100

            # GSI tipo-timestamp-index: PK="medicion" constante, SK=timestamp DESC
            resp = tabla.query(
                IndexName="tipo-timestamp-index",
                KeyConditionExpression=Key("tipo_registro").eq("medicion"),
                ScanIndexForward=False,   # más reciente primero
                Limit=limite,
            )
            items = _agregar_url_imagen(resp.get("Items", []))
            return _respuesta(200, {
                "total": len(items),
                "mediciones": items,
            })

        # ── GET /mediciones/{id_punto} ───────────────────────────────────────
        elif id_punto:
            qp = event.get("queryStringParameters") or {}
            limite = min(int(qp.get("limite", 50)), 200)

            resp = tabla.query(
                KeyConditionExpression=Key("id_punto").eq(id_punto),
                ScanIndexForward=False,   # más reciente primero
                Limit=limite,
            )
            items = _agregar_url_imagen(resp.get("Items", []))
            return _respuesta(200, {
                "id_punto": id_punto,
                "total": len(items),
                "mediciones": items,
            })

        return _respuesta(400, {"error": "Ruta no reconocida"})

    except Exception as e:
        logger.exception("Error en api_mediciones: %s", e)
        return _respuesta(500, {"error": str(e)})
