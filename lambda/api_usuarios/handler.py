"""
CorrIA - Lambda de gestión de usuarios.

Rutas:
  POST   /usuarios                        → crear usuario (solo admin)
  GET    /usuarios                        → listar usuarios (solo admin)
  GET    /usuarios/me                     → perfil propio (cualquier rol)
  PUT    /usuarios/me                     → actualizar perfil propio (cualquier rol)
  PUT    /usuarios/{id_usuario}           → actualizar usuario (solo admin)
  DELETE /usuarios/{id_usuario}/eliminar  → eliminar usuario permanentemente (solo admin)
  DELETE /usuarios/{id_usuario}           → deshabilitar usuario (solo admin)
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLA_USUARIOS = os.environ["TABLA_USUARIOS"]
USER_POOL_ID = os.environ["USER_POOL_ID"]
REGION = os.environ["REGION"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)
tabla = dynamodb.Table(TABLA_USUARIOS)
cognito = boto3.client("cognito-idp", region_name=REGION)

ROLES_VALIDOS = {"admin", "tecnico", "cliente"}

# Campos que solo puede modificar el admin
CAMPOS_PROTEGIDOS_ME = {"email", "rol", "empresa", "id_usuario", "cognito_sub", "fecha_creacion"}
# Campos que el usuario puede modificar en su propio perfil
CAMPOS_PERMITIDOS_ME = {"nombre", "telefono", "cargo", "avatar_color", "fecha_ultimo_login"}
# Campos que el admin puede modificar en cualquier usuario (email es inmutable en Cognito)
CAMPOS_PERMITIDOS_ADMIN = {"nombre", "rol", "empresa", "telefono", "cargo"}


def _respuesta(codigo: int, cuerpo) -> dict:
    return {
        "statusCode": codigo,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(cuerpo, ensure_ascii=False, default=str),
    }


def _claims(event: dict) -> dict:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )


def _es_admin(event: dict) -> bool:
    grupos = _claims(event).get("cognito:groups", "") or ""
    return "admin" in grupos.split(",")


def _email_usuario(event: dict) -> str:
    return _claims(event).get("email", "")


def _buscar_por_email(email: str) -> dict | None:
    """Busca un usuario en DynamoDB por email usando el GSI email-index."""
    resp = tabla.query(
        IndexName="email-index",
        KeyConditionExpression=Key("email").eq(email),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _crear_usuario_basico(email: str, claims: dict) -> dict:
    """Crea un registro mínimo en DynamoDB para usuario de Cognito sin registro previo."""
    grupos = claims.get("cognito:groups", "") or ""
    rol = "cliente"
    for g in grupos.split(","):
        if g.strip() in ROLES_VALIDOS:
            rol = g.strip()
            break

    id_usuario = f"USR-{uuid.uuid4()}"
    item = {
        "id_usuario": id_usuario,
        "email": email,
        "cognito_sub": claims.get("sub", ""),
        "rol": rol,
        "empresa": "Corpacero",
        "nombre": claims.get("given_name", ""),
        "fecha_creacion": datetime.now(timezone.utc).isoformat(),
    }
    tabla.put_item(Item=item)
    return item


def lambda_handler(event: dict, context) -> dict:
    metodo = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    id_usuario = path_params.get("id_usuario")

    try:
        # ── GET /usuarios/me ─────────────────────────────────────────────────
        if metodo == "GET" and resource == "/usuarios/me":
            email = _email_usuario(event)
            if not email:
                return _respuesta(401, {"error": "No se pudo determinar el usuario"})

            usuario = _buscar_por_email(email)
            if not usuario:
                # Usuario existe en Cognito pero no en DynamoDB — crear registro básico
                usuario = _crear_usuario_basico(email, _claims(event))

            return _respuesta(200, usuario)

        # ── PUT /usuarios/me ─────────────────────────────────────────────────
        elif metodo == "PUT" and resource == "/usuarios/me":
            email = _email_usuario(event)
            if not email:
                return _respuesta(401, {"error": "No se pudo determinar el usuario"})

            usuario = _buscar_por_email(email)
            if not usuario:
                usuario = _crear_usuario_basico(email, _claims(event))

            body = json.loads(event.get("body") or "{}")
            # Filtrar silenciosamente campos protegidos — solo aplicar los permitidos
            campos = {k: v for k, v in body.items() if k in CAMPOS_PERMITIDOS_ME}
            if not campos:
                return _respuesta(400, {"error": "No hay campos válidos para actualizar"})

            expr = "SET " + ", ".join(f"#{k} = :{k}" for k in campos)
            nombres = {f"#{k}": k for k in campos}
            valores = {f":{k}": v for k, v in campos.items()}

            tabla.update_item(
                Key={"id_usuario": usuario["id_usuario"]},
                UpdateExpression=expr,
                ExpressionAttributeNames=nombres,
                ExpressionAttributeValues=valores,
            )
            return _respuesta(200, {"mensaje": "Perfil actualizado"})

        # ── GET /usuarios — listar (solo admin) ──────────────────────────────
        elif metodo == "GET" and resource == "/usuarios":
            if not _es_admin(event):
                return _respuesta(403, {"error": "Solo administradores pueden listar usuarios"})
            resp = tabla.scan()
            return _respuesta(200, resp.get("Items", []))

        # ── POST /usuarios — crear (solo admin) ──────────────────────────────
        elif metodo == "POST" and resource == "/usuarios":
            if not _es_admin(event):
                return _respuesta(403, {"error": "Solo administradores pueden crear usuarios"})

            body = json.loads(event.get("body") or "{}")
            email = body.get("email")
            nombre = body.get("nombre", "")
            cargo = body.get("cargo", "")
            rol = body.get("rol", "cliente")
            empresa = body.get("empresa", "Corpacero")

            if not email:
                return _respuesta(400, {"error": "email es requerido"})
            if rol not in ROLES_VALIDOS:
                return _respuesta(400, {"error": f"rol debe ser uno de: {ROLES_VALIDOS}"})

            # Cognito genera la contraseña temporal y la envía por email al usuario nuevo
            resp_cognito = cognito.admin_create_user(
                UserPoolId=USER_POOL_ID,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "given_name", "Value": nombre},
                ],
                DesiredDeliveryMediums=["EMAIL"],
            )

            cognito_sub = next(
                (a["Value"] for a in resp_cognito["User"]["Attributes"] if a["Name"] == "sub"),
                None,
            )

            # Asignar al grupo de Cognito correspondiente al rol
            cognito.admin_add_user_to_group(
                UserPoolId=USER_POOL_ID,
                Username=email,
                GroupName=rol,
            )

            id_usuario_nuevo = f"USR-{uuid.uuid4()}"
            item = {
                "id_usuario": id_usuario_nuevo,
                "email": email,
                "cognito_sub": cognito_sub,
                "rol": rol,
                "empresa": empresa,
                "nombre": nombre,
                "cargo": cargo,
                "fecha_creacion": datetime.now(timezone.utc).isoformat(),
                "activo": True,
            }
            tabla.put_item(Item=item)

            return _respuesta(201, {"mensaje": f"Usuario creado. Se envió contraseña temporal a {email}"})

        # ── PUT /usuarios/{id_usuario} — actualizar o rehabilitar (solo admin) ──
        elif metodo == "PUT" and resource == "/usuarios/{id_usuario}" and id_usuario:
            if not _es_admin(event):
                return _respuesta(403, {"error": "Solo administradores pueden modificar usuarios"})

            body = json.loads(event.get("body") or "{}")

            # Buscar siempre por PK directamente, nunca por GSI
            resp = tabla.get_item(Key={"id_usuario": id_usuario})
            usuario = resp.get("Item")
            if not usuario:
                return _respuesta(404, {"error": f"Usuario {id_usuario} no encontrado"})

            # ── Flujo de reactivación ────────────────────────────────────────
            if body.get("reactivar") is True:
                email_objetivo = usuario.get("email")
                if not email_objetivo:
                    return _respuesta(500, {"error": "El usuario no tiene email registrado en DynamoDB"})

                # Rehabilitar en Cognito (permite volver a hacer login)
                cognito.admin_enable_user(
                    UserPoolId=USER_POOL_ID,
                    Username=email_objetivo,
                )

                # Marcar como activo y eliminar campos de deshabilitación
                ahora = datetime.now(timezone.utc).isoformat()
                tabla.update_item(
                    Key={"id_usuario": id_usuario},
                    UpdateExpression="SET #activo = :verdad, #fecha_reac = :ahora REMOVE #fecha_des",
                    ExpressionAttributeNames={
                        "#activo": "activo",
                        "#fecha_reac": "fecha_reactivacion",
                        "#fecha_des": "fecha_deshabilitacion",
                    },
                    ExpressionAttributeValues={
                        ":verdad": True,
                        ":ahora": ahora,
                    },
                )
                usuario_actualizado = tabla.get_item(Key={"id_usuario": id_usuario}).get("Item", {})
                return _respuesta(200, usuario_actualizado)

            # ── Flujo de actualización de campos ─────────────────────────────
            # email es inmutable — filtrar campos permitidos para admin
            campos = {k: v for k, v in body.items() if k in CAMPOS_PERMITIDOS_ADMIN}
            if not campos:
                return _respuesta(400, {"error": "No hay campos válidos para actualizar"})

            rol_nuevo = campos.get("rol")
            rol_actual = usuario.get("rol")

            # Si cambia el rol, actualizar grupos en Cognito
            if rol_nuevo and rol_nuevo != rol_actual:
                if rol_nuevo not in ROLES_VALIDOS:
                    return _respuesta(400, {"error": f"rol debe ser uno de: {ROLES_VALIDOS}"})
                email_usuario = usuario.get("email")
                if rol_actual in ROLES_VALIDOS:
                    cognito.admin_remove_user_from_group(
                        UserPoolId=USER_POOL_ID,
                        Username=email_usuario,
                        GroupName=rol_actual,
                    )
                cognito.admin_add_user_to_group(
                    UserPoolId=USER_POOL_ID,
                    Username=email_usuario,
                    GroupName=rol_nuevo,
                )

            expr = "SET " + ", ".join(f"#{k} = :{k}" for k in campos)
            nombres = {f"#{k}": k for k in campos}
            valores = {f":{k}": v for k, v in campos.items()}

            tabla.update_item(
                Key={"id_usuario": id_usuario},
                UpdateExpression=expr,
                ExpressionAttributeNames=nombres,
                ExpressionAttributeValues=valores,
            )
            return _respuesta(200, {"mensaje": "Usuario actualizado", "id_usuario": id_usuario})

        # ── DELETE /usuarios/{id_usuario}/eliminar — eliminación permanente (solo admin) ──
        elif metodo == "DELETE" and resource == "/usuarios/{id_usuario}/eliminar" and id_usuario:
            if not _es_admin(event):
                return _respuesta(403, {"error": "Solo administradores pueden eliminar usuarios"})

            # Evitar que el admin se elimine a sí mismo
            email_propio = _email_usuario(event)
            resp = tabla.get_item(Key={"id_usuario": id_usuario})
            usuario = resp.get("Item")
            if not usuario:
                return _respuesta(404, {"error": f"Usuario {id_usuario} no encontrado"})

            if usuario.get("email") == email_propio:
                return _respuesta(400, {"error": "No puedes eliminar tu propia cuenta"})

            email_objetivo = usuario.get("email")
            if not email_objetivo:
                return _respuesta(500, {"error": "El usuario no tiene email registrado en DynamoDB"})

            # Eliminar permanentemente de Cognito
            cognito.admin_delete_user(
                UserPoolId=USER_POOL_ID,
                Username=email_objetivo,
            )

            # Eliminar registro de DynamoDB
            tabla.delete_item(Key={"id_usuario": id_usuario})

            return _respuesta(200, {"mensaje": "Usuario eliminado permanentemente"})

        # ── DELETE /usuarios/{id_usuario} — deshabilitar (solo admin) ─────────
        elif metodo == "DELETE" and resource == "/usuarios/{id_usuario}" and id_usuario:
            if not _es_admin(event):
                return _respuesta(403, {"error": "Solo administradores pueden deshabilitar usuarios"})

            resp = tabla.get_item(Key={"id_usuario": id_usuario})
            usuario = resp.get("Item")
            if not usuario:
                return _respuesta(404, {"error": f"Usuario {id_usuario} no encontrado"})

            email_objetivo = usuario.get("email")
            if not email_objetivo:
                return _respuesta(500, {"error": "El usuario no tiene email registrado en DynamoDB"})

            # Deshabilitar en Cognito (bloquea el login sin eliminar el usuario)
            cognito.admin_disable_user(
                UserPoolId=USER_POOL_ID,
                Username=email_objetivo,
            )

            # Marcar como inactivo en DynamoDB
            ahora = datetime.now(timezone.utc).isoformat()
            tabla.update_item(
                Key={"id_usuario": id_usuario},
                UpdateExpression="SET #activo = :falso, #fecha_des = :ahora",
                ExpressionAttributeNames={
                    "#activo": "activo",
                    "#fecha_des": "fecha_deshabilitacion",
                },
                ExpressionAttributeValues={
                    ":falso": False,
                    ":ahora": ahora,
                },
            )
            return _respuesta(200, {"mensaje": "Usuario deshabilitado correctamente"})

        return _respuesta(405, {"error": f"Método {metodo} no permitido"})

    except cognito.exceptions.UsernameExistsException:
        return _respuesta(409, {"error": "Ya existe un usuario con ese email"})
    except cognito.exceptions.UserNotFoundException:
        return _respuesta(404, {"error": "Usuario no encontrado en Cognito"})
    except Exception as e:
        logger.exception("Error en api_usuarios: %s", e)
        return _respuesta(500, {"error": str(e)})
