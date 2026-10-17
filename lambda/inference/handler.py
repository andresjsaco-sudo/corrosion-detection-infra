"""
CorrIA - Lambda de inferencia YOLOv8-seg ONNX.

Recibe una imagen en base64 + información de ubicación, resuelve o crea el
punto de medición automáticamente (flujo bottom-up), corre inferencia ONNX,
guarda imagen + resultado en S3 y registra la medición en DynamoDB.
"""
import base64
import io
import json
import logging
import os
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key
from PIL import Image
import numpy as np
import onnxruntime as ort

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Variables de entorno inyectadas por CDK
BUCKET_NAME = os.environ["BUCKET_NAME"]
TABLA_PUNTOS = os.environ["TABLA_PUNTOS"]
TABLA_MEDICIONES = os.environ["TABLA_MEDICIONES"]
REGION = os.environ["REGION"]

# Ruta al modelo ONNX empaquetado con la Lambda
RUTA_MODELO = Path(__file__).parent / "model" / "yolov8n-seg.onnx"

# Sesión ONNX — se inicializa una sola vez en el cold start
_sesion_onnx: ort.InferenceSession | None = None

s3 = boto3.client("s3", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
tabla_puntos = dynamodb.Table(TABLA_PUNTOS)
tabla_mediciones = dynamodb.Table(TABLA_MEDICIONES)


# ── Helpers de tipo ─────────────────────────────────────────────────────────

def floats_to_decimal(obj):
    """Convierte recursivamente floats a Decimal para compatibilidad con DynamoDB."""
    if isinstance(obj, list):
        return [floats_to_decimal(x) for x in obj]
    elif isinstance(obj, dict):
        return {k: floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    return obj


class DecimalEncoder(json.JSONEncoder):
    """Convierte Decimal a float al serializar la respuesta JSON para el cliente."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _respuesta(codigo: int, cuerpo: dict) -> dict:
    return {
        "statusCode": codigo,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(cuerpo, ensure_ascii=False, cls=DecimalEncoder),
    }


# ── Lógica de resolución de punto (bottom-up) ───────────────────────────────

def _buscar_por_clave_logica(clave_logica: str) -> dict | None:
    """Consulta el GSI ClaveLogicaIndex; devuelve el item si existe, None si no."""
    resp = tabla_puntos.query(
        IndexName="ClaveLogicaIndex",
        KeyConditionExpression=Key("clave_logica").eq(clave_logica),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _crear_punto(datos: dict) -> dict:
    """Crea un nuevo punto en DynamoDB y lo devuelve."""
    item = floats_to_decimal(datos)
    tabla_puntos.put_item(Item=item)
    logger.info("Punto creado: %s (%s)", datos["id_punto"], datos.get("clave_logica"))
    return datos  # devolver con tipos Python originales para la respuesta


def _resolver_punto(ubicacion: dict, ts: str) -> tuple[str, dict]:
    """
    Resuelve el id_punto según el modo de la ubicación.
    Devuelve (id_punto, info_punto) donde info_punto es el dict del punto.

    Modos:
      - planta_existente: usa id_punto provisto, falla si no existe
      - planta_nueva: busca por clave_logica o crea nuevo punto
      - coordenadas_libres: busca por clave "libre-{lat}-{lng}" o crea nuevo punto
    """
    modo = ubicacion.get("modo")

    if modo == "planta_existente":
        id_punto = ubicacion.get("id_punto")
        if not id_punto:
            raise ValueError("modo=planta_existente requiere id_punto")
        resp = tabla_puntos.get_item(Key={"id_punto": id_punto})
        punto = resp.get("Item")
        if not punto:
            raise LookupError(f"Punto {id_punto} no encontrado")
        return id_punto, punto

    elif modo == "planta_nueva":
        empresa = ubicacion.get("empresa", "Planta demo")
        sede = ubicacion.get("sede", "")
        ciudad = ubicacion.get("ciudad", "")
        if not sede or not ciudad:
            raise ValueError("modo=planta_nueva requiere sede y ciudad")

        clave_logica = f"{empresa}-{sede}-{ciudad}"
        existente = _buscar_por_clave_logica(clave_logica)
        if existente:
            logger.info("Punto existente encontrado por clave_logica: %s", clave_logica)
            return existente["id_punto"], existente

        coordenadas = ubicacion.get("coordenadas", {})
        nuevo = {
            "id_punto": f"PT-{uuid.uuid4()}",
            "clave_logica": clave_logica,
            "coordenadas": coordenadas,
            "ciudad": ciudad,
            "departamento": ubicacion.get("departamento", ""),
            "tipo_material": ubicacion.get("tipo_material", ""),
            "tipo_estructura": ubicacion.get("tipo_estructura", "otro"),
            "empresa": empresa,
            "sede": sede,
            "fecha_creacion": ts,
        }
        _crear_punto(nuevo)
        return nuevo["id_punto"], nuevo

    elif modo == "coordenadas_libres":
        lat = ubicacion.get("latitud")
        lng = ubicacion.get("longitud")
        if lat is None or lng is None:
            raise ValueError("modo=coordenadas_libres requiere latitud y longitud")

        descripcion = ubicacion.get("descripcion", f"Punto {lat},{lng}")
        # Redondear a 4 decimales para que coordenadas muy cercanas sean el mismo punto
        clave_logica = f"libre-{round(lat, 4)}-{round(lng, 4)}"
        existente = _buscar_por_clave_logica(clave_logica)
        if existente:
            logger.info("Punto libre existente encontrado: %s", clave_logica)
            return existente["id_punto"], existente

        nuevo = {
            "id_punto": f"PT-{uuid.uuid4()}",
            "clave_logica": clave_logica,
            "coordenadas": {"lat": lat, "lng": lng},
            "ciudad": "",
            "departamento": "",
            "tipo_material": "",
            "tipo_estructura": "otro",
            "empresa": "Sin empresa",
            "sede": descripcion,
            "fecha_creacion": ts,
        }
        _crear_punto(nuevo)
        return nuevo["id_punto"], nuevo

    else:
        raise ValueError(f"modo de ubicación inválido: '{modo}'. Use planta_existente, planta_nueva o coordenadas_libres")


# ── Inferencia ONNX ──────────────────────────────────────────────────────────

def _obtener_sesion() -> ort.InferenceSession:
    """Carga el modelo ONNX una sola vez y lo reutiliza entre invocaciones."""
    global _sesion_onnx
    if _sesion_onnx is None:
        logger.info("Cargando modelo ONNX desde %s", RUTA_MODELO)
        _sesion_onnx = ort.InferenceSession(
            str(RUTA_MODELO),
            providers=["CPUExecutionProvider"],
        )
    return _sesion_onnx


def _preprocesar(imagen: Image.Image, tamano: int = 640) -> np.ndarray:
    """Redimensiona y normaliza la imagen para YOLOv8."""
    imagen = imagen.resize((tamano, tamano))
    arr = np.array(imagen, dtype=np.float32) / 255.0
    # HWC → CHW → NCHW
    arr = arr.transpose(2, 0, 1)[np.newaxis, ...]
    return arr


def _calcular_nivel(area_pct: float) -> int:
    """
    Convierte porcentaje de área corroída a nivel discreto:
      0% → 0 (sin corrosión)
      1-10% → 1 (leve)
      10-30% → 2 (moderada)
      >30% → 3 (severa)
    """
    if area_pct <= 0:
        return 0
    elif area_pct <= 10:
        return 1
    elif area_pct <= 30:
        return 2
    return 3


def _inferir(imagen: Image.Image) -> dict:
    """
    Corre la inferencia ONNX y devuelve nivel y porcentaje de área.

    YOLOv8-seg devuelve dos salidas:
      output0: [1, 116, 8400] — detecciones (x, y, w, h, conf, cls, mask_coefs)
      output1: [1, 32, 160, 160] — prototipos de máscara

    Para el placeholder (modelo vacío) devuelve área 0.
    """
    sesion = _obtener_sesion()
    arr = _preprocesar(imagen)

    nombre_entrada = sesion.get_inputs()[0].name
    salidas = sesion.run(None, {nombre_entrada: arr})

    detecciones = salidas[0]  # shape [1, 116, 8400]
    dets = detecciones[0].T   # → [8400, 116]

    area_total_px = 640 * 640
    area_corroida_px = 0
    confianza_minima = 0.5

    for det in dets:
        if det[4] < confianza_minima:
            continue
        # Columnas 0-3: xywh normalizado — área estimada como bbox
        _, _, w, h = det[:4]
        area_corroida_px += int(w * h * area_total_px)

    area_pct = min((area_corroida_px / area_total_px) * 100, 100.0)

    return {
        "nivel_corrosion": _calcular_nivel(area_pct),
        "area_corroida_pct": round(area_pct, 2),
        "confianza_promedio": float(np.max(dets[:, 4]) if len(dets) > 0 else 0.0),
    }


# ── Handler principal ────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body", "{}"))

        imagen_b64 = body.get("imagen_base64")
        fuente = body.get("fuente", "movil")
        ubicacion = body.get("ubicacion")
        inferencia_local = body.get("inferencia_local", None)
        latitud_real = body.get("latitud_real")
        longitud_real = body.get("longitud_real")
        notas = body.get("notas", "")

        if not imagen_b64:
            return _respuesta(400, {"error": "imagen_base64 es requerido"})
        if not ubicacion or not ubicacion.get("modo"):
            return _respuesta(400, {"error": "ubicacion.modo es requerido (planta_existente | planta_nueva | coordenadas_libres)"})

        ts = datetime.now(timezone.utc).isoformat()

        # 1. Resolver o crear el punto de medición
        try:
            id_punto, info_punto = _resolver_punto(ubicacion, ts)
        except LookupError as e:
            return _respuesta(404, {"error": str(e)})
        except ValueError as e:
            return _respuesta(400, {"error": str(e)})

        # 2. Decodificar imagen
        datos_imagen = base64.b64decode(imagen_b64)
        imagen = Image.open(io.BytesIO(datos_imagen)).convert("RGB")

        # 3. Inferencia ONNX
        resultado_ml = _inferir(imagen)

        # 4. Generar IDs únicos para esta medición
        id_medicion = f"MED-{uuid.uuid4()}"
        s3_key_imagen = f"raw/{id_punto}/{id_medicion}.jpg"
        s3_key_resultado = f"resultados/{id_punto}/{id_medicion}.json"

        # 5. Guardar imagen original en S3
        buffer = io.BytesIO()
        imagen.save(buffer, format="JPEG", quality=85)
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key_imagen,
            Body=buffer.getvalue(),
            ContentType="image/jpeg",
        )

        # 6. Armar resultado completo (con tipos Python nativos para S3/respuesta)
        resultado_completo = {
            "id_medicion": id_medicion,
            "id_punto": id_punto,
            "timestamp": ts,
            "fuente": fuente,
            **resultado_ml,
            "latitud_real": latitud_real,
            "longitud_real": longitud_real,
            "notas": notas,
            "inferencia_local": inferencia_local,
            "s3_key_imagen": s3_key_imagen,
            "s3_key_resultado": s3_key_resultado,
        }

        # 7. Guardar resultado JSON en S3
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key_resultado,
            Body=json.dumps(resultado_completo, ensure_ascii=False, cls=DecimalEncoder),
            ContentType="application/json",
        )

        # 8. Insertar medición en DynamoDB (floats → Decimal)
        item_db = {
            "id_punto": id_punto,
            "timestamp": ts,
            "tipo_registro": "medicion",  # PK del GSI tipo-timestamp-index
            "id_medicion": id_medicion,
            "nivel_corrosion": resultado_ml["nivel_corrosion"],
            "area_corroida_pct": str(resultado_ml["area_corroida_pct"]),
            "s3_key_imagen": s3_key_imagen,
            "s3_key_resultado": s3_key_resultado,
            "fuente": fuente,
            "notas": notas,
            "inferencia_local": floats_to_decimal(inferencia_local or {}),
        }
        if latitud_real is not None:
            item_db["latitud_real"] = Decimal(str(latitud_real))
        if longitud_real is not None:
            item_db["longitud_real"] = Decimal(str(longitud_real))

        tabla_mediciones.put_item(Item=item_db)

        logger.info(
            "Medición %s registrada: punto=%s, nivel=%d, área=%.1f%%",
            id_medicion, id_punto,
            resultado_ml["nivel_corrosion"],
            resultado_ml["area_corroida_pct"],
        )

        # 9. Respuesta enriquecida: medición + info del punto
        return _respuesta(200, {
            **resultado_completo,
            "punto_info": {
                "id_punto": id_punto,
                "sede": info_punto.get("sede", ""),
                "ciudad": info_punto.get("ciudad", ""),
                "empresa": info_punto.get("empresa", ""),
                "coordenadas": info_punto.get("coordenadas", {}),
                "clave_logica": info_punto.get("clave_logica", ""),
            },
        })

    except Exception as e:
        logger.exception("Error en inferencia: %s", e)
        return _respuesta(500, {"error": str(e)})
