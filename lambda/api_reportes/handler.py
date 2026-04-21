"""
CorrIA - Lambda de reportes.

Rutas (accesibles por todos los roles):
  GET /reportes/planta/{id_punto}?desde=ISO&hasta=ISO
  GET /reportes/global?desde=ISO&hasta=ISO

Si no se pasan parámetros de fechas, se usan los últimos 30 días por defecto.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLA_PUNTOS = os.environ["TABLA_PUNTOS"]
TABLA_MEDICIONES = os.environ["TABLA_MEDICIONES"]
REGION = os.environ["REGION"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
tabla_puntos = dynamodb.Table(TABLA_PUNTOS)
tabla_mediciones = dynamodb.Table(TABLA_MEDICIONES)


# ── Helpers ───────────────────────────────────────────────────────────────────

class DecimalEncoder(json.JSONEncoder):
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


def _fechas_por_defecto(qp: dict) -> tuple[str, str]:
    """Devuelve (desde, hasta) en ISO8601 — últimos 30 días si no se especifican."""
    ahora = datetime.now(timezone.utc)
    hasta = qp.get("hasta") or ahora.isoformat()
    desde = qp.get("desde") or (ahora - timedelta(days=30)).isoformat()
    return desde, hasta


def _mediciones_por_punto(id_punto: str, desde: str, hasta: str) -> list:
    """Consulta mediciones de un punto en el rango de fechas dado."""
    resp = tabla_mediciones.query(
        KeyConditionExpression=(
            Key("id_punto").eq(id_punto) & Key("timestamp").between(desde, hasta)
        ),
    )
    items = resp.get("Items", [])
    # Paginar si hay más resultados
    while "LastEvaluatedKey" in resp:
        resp = tabla_mediciones.query(
            KeyConditionExpression=(
                Key("id_punto").eq(id_punto) & Key("timestamp").between(desde, hasta)
            ),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items", []))
    return sorted(items, key=lambda x: x.get("timestamp", ""))


def _calcular_resumen(mediciones: list) -> dict:
    """Calcula estadísticas de un conjunto de mediciones."""
    if not mediciones:
        return {
            "total_mediciones": 0,
            "nivel_promedio": 0.0,
            "nivel_maximo": 0,
            "area_promedio_pct": 0.0,
            "total_alertas": 0,
            "tendencia": "estable",
        }

    niveles = [float(m.get("nivel_corrosion", 0)) for m in mediciones]
    areas = [float(m.get("area_afectada_pct", 0)) for m in mediciones if m.get("area_afectada_pct") is not None]
    alertas = [m for m in mediciones if float(m.get("nivel_corrosion", 0)) >= 2]

    nivel_promedio = sum(niveles) / len(niveles)
    area_promedio = sum(areas) / len(areas) if areas else 0.0

    # Calcular tendencia dividiendo en primera y segunda mitad del periodo
    tendencia = "estable"
    if len(mediciones) >= 2:
        mitad = len(niveles) // 2
        promedio_primera = sum(niveles[:mitad]) / mitad if mitad else 0
        promedio_segunda = sum(niveles[mitad:]) / (len(niveles) - mitad)
        if promedio_segunda > promedio_primera + 0.5:
            tendencia = "empeorando"
        elif promedio_segunda < promedio_primera - 0.5:
            tendencia = "mejorando"

    return {
        "total_mediciones": len(mediciones),
        "nivel_promedio": round(nivel_promedio, 2),
        "nivel_maximo": int(max(niveles)),
        "area_promedio_pct": round(area_promedio, 2),
        "total_alertas": len(alertas),
        "tendencia": tendencia,
    }


# ── Handler principal ─────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    metodo = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    qp = event.get("queryStringParameters") or {}

    try:
        # ── GET /reportes/planta/{id_punto} ───────────────────────────────────
        if metodo == "GET" and resource == "/reportes/planta/{id_punto}":
            id_punto = path_params.get("id_punto")
            if not id_punto:
                return _respuesta(400, {"error": "id_punto es requerido"})

            desde, hasta = _fechas_por_defecto(qp)

            resp_punto = tabla_puntos.get_item(Key={"id_punto": id_punto})
            punto = resp_punto.get("Item")
            if not punto:
                return _respuesta(404, {"error": f"Punto {id_punto} no encontrado"})

            mediciones = _mediciones_por_punto(id_punto, desde, hasta)
            resumen = _calcular_resumen(mediciones)
            alertas = [m for m in mediciones if float(m.get("nivel_corrosion", 0)) >= 2]

            return _respuesta(200, {
                "punto": punto,
                "periodo": {"desde": desde, "hasta": hasta},
                "resumen": resumen,
                "mediciones": mediciones,
                "alertas": alertas,
            })

        # ── GET /reportes/global ──────────────────────────────────────────────
        elif metodo == "GET" and resource == "/reportes/global":
            desde, hasta = _fechas_por_defecto(qp)

            # Obtener todos los puntos
            resp_puntos = tabla_puntos.scan()
            puntos = resp_puntos.get("Items", [])
            while "LastEvaluatedKey" in resp_puntos:
                resp_puntos = tabla_puntos.scan(ExclusiveStartKey=resp_puntos["LastEvaluatedKey"])
                puntos.extend(resp_puntos.get("Items", []))

            por_planta = []
            total_mediciones_global = 0
            total_nivel_global = []
            plantas_criticas = 0
            todas_alertas = []

            for punto in puntos:
                id_punto = punto["id_punto"]
                mediciones = _mediciones_por_punto(id_punto, desde, hasta)
                resumen = _calcular_resumen(mediciones)

                por_planta.append({"punto": punto, "resumen": resumen})
                total_mediciones_global += resumen["total_mediciones"]

                if mediciones:
                    total_nivel_global.extend(
                        float(m.get("nivel_corrosion", 0)) for m in mediciones
                    )

                if resumen["total_alertas"] > 0:
                    plantas_criticas += 1

                alertas_planta = [m for m in mediciones if float(m.get("nivel_corrosion", 0)) >= 2]
                todas_alertas.extend(alertas_planta)

            nivel_promedio_global = (
                round(sum(total_nivel_global) / len(total_nivel_global), 2)
                if total_nivel_global else 0.0
            )

            # Top 5 mediciones más críticas: mayor nivel, luego mayor área
            top_alertas = sorted(
                todas_alertas,
                key=lambda m: (
                    -float(m.get("nivel_corrosion", 0)),
                    -float(m.get("area_afectada_pct", 0)),
                ),
            )[:5]

            return _respuesta(200, {
                "periodo": {"desde": desde, "hasta": hasta},
                "resumen_global": {
                    "total_plantas": len(puntos),
                    "total_mediciones": total_mediciones_global,
                    "plantas_criticas": plantas_criticas,
                    "nivel_promedio_global": nivel_promedio_global,
                },
                "por_planta": por_planta,
                "top_alertas": top_alertas,
            })

        return _respuesta(405, {"error": f"Método {metodo} no permitido"})

    except Exception as e:
        logger.exception("Error en api_reportes: %s", e)
        return _respuesta(500, {"error": str(e)})
