"""
CorrIA - Lambda de alertas.
GET /alertas → mediciones con nivel_corrosion >= 2 (moderada/severa) de las últimas 24h.
"""
import json
import logging
import os
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLA_MEDICIONES = os.environ["TABLA_MEDICIONES"]
REGION = os.environ["REGION"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
tabla = dynamodb.Table(TABLA_MEDICIONES)


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


def lambda_handler(event: dict, context) -> dict:
    try:
        query_params = event.get("queryStringParameters") or {}
        horas = int(query_params.get("horas", 24))
        nivel_minimo = int(query_params.get("nivel_minimo", 2))

        # Timestamp de corte
        desde = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()

        # Usar el GSI nivel-timestamp-index para niveles moderado (2) y severo (3)
        alertas = []
        for nivel in range(nivel_minimo, 4):
            resp = tabla.query(
                IndexName="nivel-timestamp-index",
                KeyConditionExpression=(
                    Key("nivel_corrosion").eq(nivel)
                    & Key("timestamp").gte(desde)
                ),
                ScanIndexForward=False,
            )
            alertas.extend(resp.get("Items", []))

        # Ordenar combinado por timestamp descendente
        alertas.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return _respuesta(200, {
            "total": len(alertas),
            "desde": desde,
            "nivel_minimo": nivel_minimo,
            "alertas": alertas,
        })

    except Exception as e:
        logger.exception("Error en api_alertas: %s", e)
        return _respuesta(500, {"error": str(e)})
