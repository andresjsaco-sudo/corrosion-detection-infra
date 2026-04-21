"""
CorrIA - Test de inferencia local.

Permite probar el handler de inferencia sin desplegar en AWS.
Requiere: pip install onnxruntime Pillow numpy boto3

Uso:
    python tests/test_inference_local.py

Variables de entorno necesarias (o usa el .env):
    BUCKET_NAME, TABLA_MEDICIONES, REGION
"""
import base64
import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Agregar la ruta del handler al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lambda/inference"))


class TestCalcularNivel(unittest.TestCase):
    """Prueba la lógica de clasificación de nivel sin necesitar el modelo."""

    def setUp(self):
        # Parchear variables de entorno y clientes AWS antes de importar el handler
        self.env_patcher = patch.dict(os.environ, {
            "BUCKET_NAME": "corria-images-test",
            "TABLA_MEDICIONES": "corria-mediciones",
            "REGION": "us-east-1",
        })
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_nivel_sin_corrosion(self):
        from handler import _calcular_nivel
        self.assertEqual(_calcular_nivel(0.0), 0)

    def test_nivel_leve(self):
        from handler import _calcular_nivel
        self.assertEqual(_calcular_nivel(5.0), 1)
        self.assertEqual(_calcular_nivel(10.0), 1)

    def test_nivel_moderado(self):
        from handler import _calcular_nivel
        self.assertEqual(_calcular_nivel(10.1), 2)
        self.assertEqual(_calcular_nivel(30.0), 2)

    def test_nivel_severo(self):
        from handler import _calcular_nivel
        self.assertEqual(_calcular_nivel(30.1), 3)
        self.assertEqual(_calcular_nivel(100.0), 3)


class TestLambdaHandler(unittest.TestCase):
    """Prueba el flujo completo del handler con mocks de AWS."""

    def _imagen_base64(self) -> str:
        """Genera una imagen de prueba 100x100 píxeles."""
        from PIL import Image
        img = Image.new("RGB", (100, 100), color=(150, 75, 0))  # color óxido
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return base64.b64encode(buf.getvalue()).decode()

    @patch.dict(os.environ, {
        "BUCKET_NAME": "corria-images-test",
        "TABLA_MEDICIONES": "corria-mediciones",
        "REGION": "us-east-1",
    })
    @patch("handler.s3")
    @patch("handler.tabla_mediciones")
    @patch("handler._inferir")
    def test_handler_exitoso(self, mock_inferir, mock_tabla, mock_s3):
        from handler import lambda_handler

        mock_inferir.return_value = {
            "nivel_corrosion": 1,
            "area_corroida_pct": 5.5,
            "confianza_promedio": 0.85,
        }
        mock_s3.put_object.return_value = {}
        mock_tabla.put_item.return_value = {}

        evento = {
            "body": json.dumps({
                "imagen_base64": self._imagen_base64(),
                "id_punto": "PT-BAQ-001",
                "fuente": "movil",
            })
        }

        resp = lambda_handler(evento, None)
        self.assertEqual(resp["statusCode"], 200)

        cuerpo = json.loads(resp["body"])
        self.assertEqual(cuerpo["nivel_corrosion"], 1)
        self.assertEqual(cuerpo["id_punto"], "PT-BAQ-001")
        self.assertIn("id_medicion", cuerpo)

    @patch.dict(os.environ, {
        "BUCKET_NAME": "corria-images-test",
        "TABLA_MEDICIONES": "corria-mediciones",
        "REGION": "us-east-1",
    })
    def test_handler_sin_imagen(self):
        from handler import lambda_handler

        evento = {"body": json.dumps({"id_punto": "PT-BAQ-001"})}
        resp = lambda_handler(evento, None)
        self.assertEqual(resp["statusCode"], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
