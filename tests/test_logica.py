"""Pruebas de la lógica de Jarvis (sin cámara, micrófono ni red).

Uso:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config  # noqa: E402
from jarvis.camera import ObsCamara  # noqa: E402
from jarvis.classifier import Clasificador, Veredicto, normalizar_titulo  # noqa: E402
from jarvis.coach import Coach  # noqa: E402
from jarvis.db import BaseDatos  # noqa: E402
from jarvis.focus import MotorFoco, Observacion, Sesion  # noqa: E402
from jarvis.intents import detectar, minutos  # noqa: E402
from jarvis.screen import Ventana  # noqa: E402

CFG = config.cargar()
TEMAS = CFG["temas"]
MATES = next(t["nombre"] for t in TEMAS if "Matemáticas" in t["nombre"])
FISICA = next(t["nombre"] for t in TEMAS if "Física" in t["nombre"])


class LLMFalso:
    """Responde como un modelo (en texto, con adornos) o falla, sin red."""

    def __init__(self, categoria="distraccion", falla=False):
        self.categoria = categoria
        self.falla = falla
        self.llamadas = 0
        self.modelo_vision = "auto"

    def chat(self, *a, **kw):
        self.llamadas += 1
        if self.falla:
            raise RuntimeError("sin red en las pruebas")
        return f"**{self.categoria}** | prueba"


def base_temporal() -> BaseDatos:
    return BaseDatos(Path(tempfile.mkdtemp()) / "prueba.db")


class Intenciones(unittest.TestCase):
    def test_iniciar_con_tema_y_tiempo(self):
        r = detectar("Hey Jarvis, voy a estudiar mates una hora", TEMAS)
        self.assertEqual((r["tipo"], r["tema"], r["total"]), ("iniciar", MATES, 60))

    def test_iniciar_tema_libre(self):
        r = detectar("empecemos, voy a repasar transformada de Fourier 40 minutos", TEMAS)
        self.assertEqual(r["tipo"], "iniciar")
        self.assertEqual(r["total"], 40)
        self.assertIn("fourier", r["tema"])

    def test_frase_real(self):
        r = detectar("Quiero estudiar pero 30 minutos. ¿Puedes configurar por un momento solo 30 minutos?", TEMAS)
        self.assertEqual((r["tipo"], r["total"], r["tema"]), ("iniciar", 30, None))

    def test_plan_de_sesion(self):
        from jarvis.intents import plan_sesion
        casos = {
            "sesión de 30 minutos": {"total": 30},
            "sesión de 30": {"total": 30},
            "una hora y media": {"total": 90},
            "2 horas en bloques de 40 con descansos de 10": {"total": 120, "bloque": 40, "descanso": 10},
            "4 pomodoros": {"total": 100, "bloque": 25, "descanso": 5},
            "sesión sin límite": {"total": 0},
            "45 minutos sin descanso": {"total": 45, "descanso": 0},
            "media hora con 5 minutos de descanso": {"total": 30, "descanso": 5},
        }
        for frase, esperado in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(plan_sesion(frase), esperado)

    def test_cambios_en_marcha(self):
        casos = {
            "dame 10 minutos más": ("extender", 10), "Jarvis, 10 más": ("extender", 10),
            "otros 20 minutos": ("extender", 20), "acórtala 15 minutos": ("acortar", 15),
            "termina en 10 minutos": ("terminar_en", 10), "dame un descanso de 15 minutos": ("descanso", 15),
        }
        for frase, (tipo, mins) in casos.items():
            with self.subTest(frase=frase):
                r = detectar(frase, TEMAS)
                self.assertEqual((r["tipo"], r["minutos"]), (tipo, mins))
        self.assertEqual(detectar("¿cuánto me falta?", TEMAS)["tipo"], "restante")
        self.assertEqual(detectar("cambia el tema a física", TEMAS)["tema"], FISICA)
        self.assertEqual(detectar("mi objetivo es hacer el ejercicio 34", TEMAS)["texto"], "hacer el ejercicio 34")
        self.assertEqual(detectar("pon los descansos de 5 minutos", TEMAS), {"tipo": "ajustar", "descanso": 5})
        self.assertEqual(detectar("bloques de 25", TEMAS), {"tipo": "ajustar", "bloque": 25})
        r = detectar("4 pomodoros de mates", TEMAS)
        self.assertEqual((r["tipo"], r["tema"], r["total"], r["bloque"]), ("iniciar", MATES, 100, 25))
        self.assertEqual(detectar("terminemos la sesión", TEMAS)["tipo"], "terminar")
        self.assertEqual(detectar("¿cómo va la sesión?", TEMAS)["tipo"], "estado")

    def test_ordenes_simples(self):
        casos = {
            "pausa": "pausa", "Jarvis, descanso de 15 minutos": "descanso", "terminemos la sesión": "terminar",
            "¿cómo voy?": "estado", "esto sí es del tema": "marcar", "cállate": "silencio",
            "sigamos": "reanudar", "no estoy con el celular, es la tableta": "no_celular",
        }
        for frase, tipo in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(detectar(frase, TEMAS)["tipo"], tipo)

    def test_marcar_no_es_del_tema(self):
        self.assertEqual(detectar("eso no es del tema", TEMAS)["categoria"], "distraccion")

    def test_preguntas_van_a_la_ia(self):
        for frase in ("¿qué es una derivada?", "eso no lo entiendo, explícame el observador"):
            with self.subTest(frase=frase):
                self.assertIsNone(detectar(frase, TEMAS))

    def test_musica(self):
        casos = {"pon música para estudiar": "play", "pausa la música": "pausa", "para la música": "pausa",
                 "siguiente canción": "siguiente", "Jarvis, sube el volumen": "subir", "pon la canción anterior": "anterior"}
        for frase, accion in casos.items():
            with self.subTest(frase=frase):
                r = detectar(frase, TEMAS)
                self.assertEqual((r["tipo"], r["accion"]), ("musica", accion))
        self.assertEqual(detectar("pausa", TEMAS)["tipo"], "pausa")   # «pausa» sola sigue siendo la sesión

    def test_que_ves(self):
        casos = {"¿Qué ventanas tengo abiertos, Jarvis?": True, "Jarvis, ahorita en ese momento, ¿qué estás viendo?": False,
                 "¿Puedes ver ahorita mismo qué estoy viendo?": False}
        for frase, listar in casos.items():
            with self.subTest(frase=frase):
                r = detectar(frase, TEMAS)
                self.assertEqual((r["tipo"], r["listar"]), ("que_ves", listar))

    def test_minutos(self):
        self.assertEqual(minutos("hora y media"), 90)
        self.assertEqual(minutos("media hora"), 30)
        self.assertEqual(minutos("dos horas"), 120)


class PalabraClave(unittest.TestCase):
    def test_separar(self):
        from jarvis.voice import separar_palabra_clave as s
        casos = {
            "Jarvis, ¿qué hora es?": (True, "¿qué hora es?"),
            "¿Qué hora es, Jarvis?": (True, "¿Qué hora es"),
            "Hey Jarvis, pon música.": (True, "pon música"),
            "Oye Yarvis pausa": (True, "pausa"),
            "Jar vis qué estoy viendo": (True, "qué estoy viendo"),
            "Jarvis.": (True, ""),
            "Harvis, ¿cómo voy?": (True, "¿cómo voy?"),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(s(texto), esperado)

    def test_no_confunde(self):
        from jarvis.voice import separar_palabra_clave as s
        for texto in ("La transformada de Laplace", "Davis dijo que el margen es bajo", "Javier, ven",
                      "el servicio de voces", "en el jardín", "Marvin y la jarra visible"):
            with self.subTest(texto=texto):
                self.assertFalse(s(texto)[0])


class Clasificacion(unittest.TestCase):
    def setUp(self):
        self.llm = LLMFalso("distraccion")
        self.c = Clasificador(CFG, base_temporal(), self.llm)
        self.c.preparar(FISICA, "ej. 34")

    def v(self, titulo, proceso="chrome.exe"):
        return Ventana(titulo=titulo, proceso=proceso, pid=1)

    def test_leer_respuesta(self):
        from jarvis.classifier import leer_respuesta
        self.assertEqual(leer_respuesta("distraccion | vídeo de fútbol"), ("distraccion", "vídeo de fútbol"))
        self.assertEqual(leer_respuesta("**Estudio** - búsqueda técnica.")[0], "estudio")
        self.assertEqual(leer_respuesta("Categoría: distracción | red social")[0], "distraccion")
        self.assertEqual(leer_respuesta("no sé")[0], "neutral")

    def test_normalizar(self):
        self.assertEqual(normalizar_titulo("(3) Instagram - Google Chrome"), "Instagram")
        self.assertEqual(normalizar_titulo("Bode - YouTube - Personal: Microsoft​ Edge"), "Bode - YouTube")

    def test_reglas(self):
        casos = [
            (self.v("(2) Instagram - Google Chrome"), "distraccion"),
            (self.v("WhatsApp - Google Chrome"), "mensajeria"),
            (self.v("Google Gemini - Google Chrome"), "herramienta"),
            (self.v("MATLAB R2025b", "matlab.exe"), "estudio"),
            (self.v("Ondas estacionarias explicado - YouTube - Google Chrome"), "estudio"),
            (self.v("", "explorer.exe"), "neutral"),
        ]
        for ventana, esperado in casos:
            with self.subTest(titulo=ventana.titulo):
                self.assertEqual(self.c.clasificar(ventana).categoria, esperado)

    def test_ia_una_sola_vez(self):
        ventana = self.v("Receta de arepas rellenas - YouTube - Google Chrome")
        self.assertEqual(self.c.clasificar(ventana).categoria, "desconocido")
        for _ in range(50):
            if not self.c._pendientes:
                break
            time.sleep(0.05)
        self.assertEqual(self.c.clasificar(ventana).categoria, "distraccion")
        self.c.clasificar(ventana)
        self.assertEqual(self.llm.llamadas, 1)

    def test_correccion_del_usuario_manda(self):
        ventana = self.v("(1) Instagram - Google Chrome")
        self.c.marcar(ventana, "estudio")
        self.assertEqual(self.c.clasificar(ventana).fuente, "usuario")


class Foco(unittest.TestCase):
    def setUp(self):
        self.motor = MotorFoco(CFG, base_temporal())
        self.s = Sesion(1, FISICA, "", 50, 10)
        self.motor.sesion_id = 1
        self.t = 1_000_000.0

    def paso(self, segundos, categoria="estudio", inactivo=0.5, cam=None):
        v = Ventana("algo", "chrome.exe", 1)
        for _ in range(segundos):
            self.t += 1
            self.motor.actualizar(Observacion(self.t, v, Veredicto(categoria), inactivo, cam), self.s)
        return self.motor.estado

    def test_distraccion_con_gracia(self):
        self.assertEqual(self.paso(5), "enfocado")
        self.assertEqual(self.paso(8, "distraccion"), "enfocado")   # aún dentro de la gracia
        self.assertEqual(self.paso(6, "distraccion"), "distraido")
        self.assertEqual(self.paso(5), "enfocado")
        self.assertEqual(self.s.distracciones, 1)
        self.assertGreater(self.s.foco_s, 10)

    def test_camara_mesa_y_ausente(self):
        abajo = ObsCamara(ok=True, presente=True, rostro=True, atencion="abajo")
        nadie = ObsCamara(ok=True, presente=False, atencion="ausente")
        self.assertEqual(self.paso(6, inactivo=10, cam=abajo), "mesa")
        self.assertEqual(self.paso(25, inactivo=40, cam=nadie), "ausente")

    def test_celular(self):
        celular = ObsCamara(ok=True, presente=True, rostro=True, atencion="abajo", celular=True)
        self.paso(5)
        self.assertEqual(self.paso(10, inactivo=10, cam=celular), "celular")

    def test_mensajeria_larga_es_distraccion(self):
        self.paso(5)
        self.assertEqual(self.paso(10, "mensajeria"), "mensajeria")
        self.assertEqual(self.paso(100, "mensajeria"), "distraido")


class CoachSinIA(unittest.TestCase):
    def test_usa_frases_fijas(self):
        dichos = []
        coach = Coach(CFG, LLMFalso(falla=True), lambda texto, prioridad, valido=None: dichos.append(texto),
                      lambda: {"tema": MATES, "objetivo": "", "plan": 50, "descanso": 10})
        coach.inicio_sesion()
        coach._pool.shutdown(wait=True)
        self.assertTrue(dichos and "Matemáticas" in dichos[0] and "cálculo" not in dichos[0])

    def test_examen_del_tema(self):
        import datetime as dt
        coach = Coach(CFG, LLMFalso(), lambda *a, **k: None, dict)
        nombre, _ = coach.proximo_examen(MATES, hoy=dt.date(2026, 10, 28))
        self.assertTrue(nombre.startswith("Matemáticas"))


class RetomarSesion(unittest.TestCase):
    def test_retoma_la_reciente_y_cierra_las_viejas(self):
        import copy

        import jarvis.app as app
        db = base_temporal()
        ahora = time.time()
        vieja = db.nueva_sesion(FISICA, "", 50, 10)
        db.ejecutar("UPDATE sesiones SET inicio = ? WHERE id = ?", (ahora - 9 * 3600, vieja))
        db.guardar_segmento(vieja, ahora - 9 * 3600, ahora - 8 * 3600, "enfocado", "", "", "estudio")
        sid = db.nueva_sesion(MATES, "exo 11", 50, 10)
        db.ejecutar("UPDATE sesiones SET inicio = ? WHERE id = ?", (ahora - 700, sid))
        db.guardar_segmento(sid, ahora - 600, ahora - 120, "enfocado", "", "", "estudio")
        db.guardar_segmento(sid, ahora - 120, ahora - 60, "distraido", "", "Instagram", "distraccion")
        cfg = copy.deepcopy(CFG)
        cfg["voz"]["activar"] = False
        cfg["camara"]["activar"] = False
        original = app.BaseDatos
        app.BaseDatos = lambda: db
        try:
            j = app.Jarvis(cfg, ui=type("UI", (), {"hablando": None, "oido": None})())
            self.assertTrue(j._retomar_sesion())
        finally:
            app.BaseDatos = original
        self.assertEqual((j.sesion.id, j.sesion.objetivo), (sid, "exo 11"))
        self.assertAlmostEqual(j.sesion.foco_s, 480, delta=2)
        self.assertAlmostEqual(j.sesion.total_s, 540, delta=2)
        self.assertIsNotNone(db.consultar("SELECT fin FROM sesiones WHERE id = ?", (vieja,))[0][0])


class SesionesDinamicas(unittest.TestCase):
    def setUp(self):
        import copy

        import jarvis.app as app
        self.app = app
        self.db = base_temporal()
        cfg = copy.deepcopy(CFG)
        cfg["voz"]["activar"] = False
        cfg["camara"]["activar"] = False
        self.dichos = []
        ui = type("UI", (), {"hablando": None, "oido": lambda s, e: None, "estado": lambda s, d: None,
                             "mensaje": lambda s, t, q: self.dichos.append(t)})()
        self._originales = (app.BaseDatos, app.generar_informe)
        app.BaseDatos = lambda: self.db
        app.generar_informe = lambda *a, **k: None   # que la prueba no toque los informes reales
        self.j = app.Jarvis(cfg, ui)
        self.j.coach.llm = LLMFalso(falla=True)

    def tearDown(self):
        self.app.BaseDatos, self.app.generar_informe = self._originales

    def test_sesion_de_30_se_cierra_sola(self):
        j = self.j
        j.ejecutar(detectar("Quiero estudiar pero 30 minutos", TEMAS), "Quiero estudiar pero 30 minutos")
        s = j.sesion
        self.assertEqual((s.total_min, s.plan_min), (30, 30))
        j.ejecutar({"tipo": "extender", "minutos": 10})
        self.assertEqual(s.total_min, 40)
        j.ejecutar({"tipo": "acortar", "minutos": 15})
        self.assertEqual(s.total_min, 25)
        s.total_s = 10 * 60
        j.ejecutar({"tipo": "terminar_en", "minutos": 5})
        self.assertEqual(s.total_min, 15)
        s.total_s = 15 * 60
        j._controlar_bloques(s, time.time())
        self.assertIsNone(j.sesion)
        fila = self.db.consultar("SELECT total_min, fin FROM sesiones WHERE id = ?", (s.id,))[0]
        self.assertEqual(fila[0], 15)
        self.assertIsNotNone(fila[1])

    def test_bloques_y_descansos(self):
        j = self.j
        j.iniciar_sesion(MATES, total=120, bloque=50, descanso=10)
        s = j.sesion
        ahora = time.time()
        s.inicio_bloque = ahora - 50 * 60
        s.total_s = 50 * 60
        j._controlar_bloques(s, ahora)
        self.assertEqual(s.modo, "descanso")
        s.fin_descanso = ahora - 1
        j._controlar_bloques(s, ahora)
        self.assertEqual(s.modo, "estudio")
        s.inicio_bloque = ahora - 50 * 60
        s.total_s = 116 * 60   # quedan 4 minutos: sin descanso, a terminar
        j._controlar_bloques(s, ahora)
        self.assertEqual(s.modo, "estudio")

    def test_pedir_tiempo_en_sesion_la_ajusta(self):
        j = self.j
        j.iniciar_sesion(FISICA)
        sid = j.sesion.id
        j.ejecutar(detectar("sesión de 45 minutos", TEMAS), "sesión de 45 minutos")
        self.assertEqual((j.sesion.id, j.sesion.total_min), (sid, 45))
        j.ejecutar(detectar("pon los descansos de 5 minutos", TEMAS), "")
        self.assertEqual(j.sesion.descanso_min, 5)

    def test_la_ia_puede_crear_sesiones(self):
        texto, acciones = self.app.acciones_de_la_ia('¡Claro que sí! [[sesion total=30 tema="Matemáticas"]]')
        self.assertEqual(texto, "¡Claro que sí!")
        self.assertEqual(acciones, [{"tipo": "iniciar", "total": 30, "tema": "Matemáticas"}])
        self.assertEqual(self.app.acciones_de_la_ia("Hecho. [[extender 10]]")[1], [{"tipo": "extender", "minutos": 10}])


class Informe(unittest.TestCase):
    def test_genera_html(self):
        from jarvis import report
        report.INFORMES = Path(tempfile.mkdtemp())   # no tocar la carpeta real de informes
        db = base_temporal()
        sid = db.nueva_sesion(MATES, "prueba", 50, 10)
        ahora = time.time()
        db.guardar_segmento(sid, ahora - 3000, ahora - 1200, "enfocado", "vlc.exe", "02 - Video", "estudio")
        db.guardar_segmento(sid, ahora - 1200, ahora - 900, "distraido", "chrome.exe", "Instagram", "distraccion")
        db.guardar_segmento(sid, ahora - 900, ahora - 10, "mesa", "", "", "estudio")
        ruta = report.generar(db, CFG)
        texto = ruta.read_text(encoding="utf-8")
        self.assertIn("Informe de foco", texto)
        self.assertIn("Instagram", texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
