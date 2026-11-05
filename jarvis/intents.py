"""Órdenes habladas o escritas que no necesitan IA: «pausa», «sesión de 30 minutos»,
«2 horas en bloques de 40», «dame 10 minutos más», «cambia el tema a física»…"""
from __future__ import annotations

import re
import unicodedata

_NUMEROS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "doce": 12, "quince": 15, "veinte": 20, "veinticinco": 25, "treinta": 30,
    "treinta y cinco": 35, "cuarenta": 40, "cuarenta y cinco": 45, "cincuenta": 50, "cincuenta y cinco": 55,
    "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento veinte": 120,
}
_NUM = r"\b(\d+|" + "|".join(sorted(map(re.escape, _NUMEROS), key=len, reverse=True)) + r")"
_MIN = r"\s*(?:m|min|mins|minuto|minutos)\b"
_HORA = r"\s*(?:h|hr|hrs|hora|horas)\b"
# (patrón, cómo convertirlo en minutos), del más específico al más general.
_PATRONES_DURACION = [
    (re.compile(r"(?:" + _NUM + r"\s+)?\bhoras?\s+y\s+media\b"),
     lambda m: (_valor(m.group(1)) if m.group(1) else 1) * 60 + 30),
    (re.compile(r"\bmedia\s+hora\b"), lambda m: 30),
    (re.compile(r"\btres\s+cuartos\s+de\s+hora\b"), lambda m: 45),
    (re.compile(r"\b(?:un\s+)?cuarto\s+de\s+hora\b"), lambda m: 15),
    (re.compile(_NUM + _HORA + r"(?:\s*y?\s*" + _NUM + _MIN + r")?"),
     lambda m: _valor(m.group(1)) * 60 + (_valor(m.group(2)) if m.group(2) else 0)),
    (re.compile(_NUM + _MIN), lambda m: _valor(m.group(1))),
]
_RELLENO = {"pero", "solo", "un", "una", "unos", "rato", "momento", "poco", "algo", "ahora", "ya", "hoy", "mismo",
            "bien", "favor", "la", "el", "los", "las", "sesion", "tema", "otra", "vez", "porfa", "de", "que", "lo"}


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    return re.sub(r"[¿?¡!.,;:«»\"()]", " ", sin_tildes).strip()


def _valor(palabra: str) -> int:
    return int(palabra) if palabra.isdigit() else _NUMEROS[palabra]


def duraciones(t: str) -> list[tuple[int, int, int]]:
    """(inicio, fin, minutos) de cada duración que aparece en un texto ya normalizado, en orden."""
    encontradas: list[tuple[int, int, int]] = []
    ocupado = [False] * len(t)
    for patron, convertir in _PATRONES_DURACION:
        for m in patron.finditer(t):
            if any(ocupado[m.start():m.end()]):
                continue
            encontradas.append((m.start(), m.end(), convertir(m)))
            ocupado[m.start():m.end()] = [True] * (m.end() - m.start())
    return sorted(encontradas)


def minutos(texto: str) -> int | None:
    d = duraciones(normalizar(texto))
    return d[0][2] if d else None


def plan_sesion(texto: str) -> dict:
    """Lo que se pide de la sesión, en minutos:
    «sesión de 30 minutos» → {"total": 30}; «2 horas en bloques de 40 con descansos de 10» →
    {"total": 120, "bloque": 40, "descanso": 10}; «4 pomodoros» → {"total": 100, "bloque": 25, "descanso": 5};
    «sin límite» → {"total": 0}."""
    t = normalizar(texto)
    plan: dict = {}
    for inicio, fin, mins in duraciones(t):
        antes, despues = t[max(0, inicio - 30):inicio], t[fin:fin + 22]
        if re.search(r"\b(?:bloques?|pomodoros?|tandas?|ciclos?|intervalos?|ratos?)\s+(?:de\s+)?(?:a\s+)?$", antes):
            plan.setdefault("bloque", mins)
        elif re.search(r"\bdescans\w*\s+(?:de\s+)?(?:a\s+)?$", antes) or re.match(r"\s*(?:de\s+)?descans", despues):
            plan.setdefault("descanso", mins)
        else:
            plan.setdefault("total", mins)
    # «bloques de 40», «descansos de 10»: ahí casi nunca se dice «minutos».
    if m := re.search(r"\b(?:bloques?|pomodoros?|tandas?|ciclos?|intervalos?)\s+de\s+" + _NUM + r"(?!\s*(?:h|hr|hrs|hora|horas)\b)", t):
        plan.setdefault("bloque", _valor(m.group(1)))
    if m := re.search(r"\bdescans\w*\s+(?:de\s+)?(?:a\s+)?" + _NUM + r"(?!\s*(?:h|hr|hrs|hora|horas)\b)", t):
        plan.setdefault("descanso", _valor(m.group(1)))
    if "pomodoro" in t:
        plan.setdefault("bloque", 25)
        plan.setdefault("descanso", 5)
        if m := re.search(_NUM + r"\s+pomodoros?", t):
            plan["total"] = _valor(m.group(1)) * plan["bloque"]
    if "total" not in plan and (m := re.search(r"\bsesion\s+de\s+" + _NUM + r"(?!\s*(?:h|hr|hrs|hora|horas)\b)", t)):
        plan["total"] = _valor(m.group(1))   # «sesión de 30»: se entienden minutos
    if re.search(r"\bsin\s+(?:limite|tiempo|fin)\b|\bhasta\s+que\s+(?:te|yo|le)\s+diga|\bindefinid", t):
        plan["total"] = 0
    if re.search(r"\bsin\s+descans", t):
        plan["descanso"] = 0
    return plan


def buscar_tema(texto: str, temas: list[dict]) -> str | None:
    t = normalizar(texto)
    for tema in temas:
        for alias in [tema["nombre"], *tema.get("alias", [])]:
            if re.search(r"\b" + re.escape(normalizar(alias)) + r"\b", t):
                return tema["nombre"]
    # Tema libre: lo que viene después de «estudiar / repasar / ver», sin tiempos ni muletillas.
    if m := re.search(r"\b(?:estudiar|repasar|ver|hacer|preparar|trabajar\s+en)\s+(.+)", t):
        libre = re.split(r"\b(?:pero|por|durante|en\s+bloques?|con|sin|hasta|mientras|y\s+luego)\b", m.group(1))[0]
        for inicio, fin, _ in reversed(duraciones(libre)):
            libre = libre[:inicio] + " " + libre[fin:]
        palabras = [w for w in libre.split() if w not in _RELLENO and not w.isdigit()]
        if palabras and len(" ".join(palabras)) >= 4:
            return " ".join(palabras)
    return None


def detectar(texto: str, temas: list[dict]) -> dict | None:
    t = normalizar(texto)
    t = re.sub(r"^(?:hey|oye|ey|ok)?\s*jarvis\b\s*", "", t)
    if not t:
        return None
    hay_tiempo = bool(duraciones(t))
    if re.search(r"\b(?:no estoy|no estaba)\b.*\b(?:celular|telefono)\b|\bes (?:la|mi) tableta\b", t):
        return {"tipo": "no_celular"}
    # Música (antes que "pausa": «pausa la música» no es pausar la sesión).
    if re.search(r"\b(?:musica|cancion|canciones|spotify|rola|playlist)\b", t):
        if re.search(r"\b(?:siguiente|otra|cambia|salta|pasa)\b", t):
            return {"tipo": "musica", "accion": "siguiente"}
        if re.search(r"\b(?:anterior|la de antes|devuelve)\b", t):
            return {"tipo": "musica", "accion": "anterior"}
        if re.search(r"\b(?:pausa|pausar|deten|detener|quita|quitar|apaga|apagar|silencia)\b", t):
            return {"tipo": "musica", "accion": "pausa"}
        if re.search(r"\b(?:pon|poner|ponme|reproduce|reproducir|dale|play|sigue|continua|activa)\b", t):
            return {"tipo": "musica", "accion": "play"}   # «pon música para estudiar» es play
        if re.search(r"\b(?:para|parar)\b", t):
            return {"tipo": "musica", "accion": "pausa"}
    if re.search(r"\b(?:sube|subir|subele|baja|bajar|bajale)\b.*\bvolumen\b", t):
        return {"tipo": "musica", "accion": "subir" if re.search(r"\bsub", t) else "bajar"}
    # Qué hay en pantalla: lo responde Jarvis con lo que realmente ve, sin IA.
    if re.search(r"\bque (?:estoy viendo|ves|estas viendo|tengo abiert\w*|ventanas|hay en (?:la|mi) pantalla)\b"
                 r"|\bpuedes ver\b|\bque estoy haciendo\b", t):
        return {"tipo": "que_ves", "listar": bool(re.search(r"\b(?:ventanas|abiert\w*)\b", t))}
    sobre_esto = r"\b(?:esto|eso|esta pagina|este video|esta ventana)\b"
    del_tema = r"\b(?:tema|estudio|clase|materia|curso)\b"
    if re.search(sobre_esto + r".*\bno\b.*\b(?:es|hace parte|cuenta|sirve)\b.*" + del_tema, t):
        return {"tipo": "marcar", "categoria": "distraccion"}
    if re.search(sobre_esto + r".*\b(?:es|hace parte|cuenta|sirve)\b.*" + del_tema, t):
        return {"tipo": "marcar", "categoria": "estudio"}
    if re.search(r"\b(?:callate|silencio|no hables|modo silencio)\b", t):
        return {"tipo": "silencio"}
    if re.search(r"\b(?:ya puedes hablar|vuelve a hablar|habla otra vez|quita el silencio)\b", t):
        return {"tipo": "hablar"}
    if re.search(r"\bcuanto\s+(?:me\s+|nos\s+|le\s+)?(?:falta|queda)\w*\b", t):
        return {"tipo": "restante"}
    if re.search(r"\b(?:como voy|como vamos|como va|cuanto llevo|resumen|estadisticas)\b", t):
        return {"tipo": "estado"}
    # --- cambiar la sesión en marcha ---------------------------------------------
    if m := re.search(r"\bobjetivo\s+(?:de\s+hoy\s+)?(?:es|sera|va\s+a\s+ser)\s+(.+)", t):
        original = re.search(r"objetivo\s+(?:de\s+hoy\s+)?(?:es|será|sera|va\s+a\s+ser)\s+(.+)", texto, re.I)
        return {"tipo": "objetivo", "texto": (original.group(1) if original else m.group(1)).strip(" .")}
    if hay_tiempo and re.search(r"\b(?:termin|acab|finaliz)\w*\b.*\ben\s+", t):
        return {"tipo": "terminar_en", "minutos": duraciones(t)[0][2]}
    if re.search(r"\b(?:termina|terminar|terminemos|acaba|acabar|finaliza|cierra)\b.*\b(?:sesion|estudio)\b"
                 r"|\bpor hoy\b|\bya termine\b|\bhasta aqui\b", t):
        return {"tipo": "terminar"}
    if hay_tiempo and re.search(r"\b(?:acorta|acortala|quita|quitale|reduce|reducela|resta|restale|menos)\b", t):
        return {"tipo": "acortar", "minutos": duraciones(t)[0][2]}
    if hay_tiempo and "descans" not in t and re.search(
            r"\b(?:mas|otros?|otras?|extra|adicionales?|alarga\w*|extiende\w*|amplia\w*|agrega\w*|suma\w*|anade\w*)\b", t):
        return {"tipo": "extender", "minutos": duraciones(t)[0][2]}
    if m := re.search(_NUM + r"\s+mas\b", t):   # «dame 10 más»
        return {"tipo": "extender", "minutos": _valor(m.group(1))}
    if re.search(r"\b(?:pausa|pausar|pausame|para un momento|detente)\b", t):
        return {"tipo": "pausa"}
    if "descans" in t:
        if re.search(r"\bdescansos\b|\b(?:cambia|pon|ajusta|configura|deja|haz)\w*\b.*\bdescanso\b"
                     r"|\bsin\s+descans|\bbloques?\b", t):
            return {"tipo": "ajustar", **plan_sesion(t)}
        return {"tipo": "descanso", "minutos": minutos(t)}
    if re.search(r"\b(?:cambia|cambiemos|pasa|pasemos|cambio|ahora)\b", t) and (tema := buscar_tema(t, temas)) \
            and any(tema == x["nombre"] for x in temas):
        return {"tipo": "cambiar_tema", "tema": tema}
    if re.search(r"\b(?:reanuda|reanudar|sigamos|continua|continuemos|volvamos|ya volvi|sigo)\b", t) and not hay_tiempo:
        return {"tipo": "reanudar"}
    if re.search(r"\bbloques?\s+de\b", t) and not re.search(r"\b(?:empez|empiez|arranc|inici|comenz|sesion)\w*", t):
        return {"tipo": "ajustar", **plan_sesion(t)}
    # --- empezar una sesión (con lo que diga de tiempo, bloques y descansos) -------
    if re.search(r"\b(?:empez|empiez|arranc|inici|comenz|comienz)\w*\b"
                 r"|\b(?:voy\s+a|vamos\s+a|quiero|necesito|toca|hay\s+que|me\s+toca)\s+(?:estudiar|repasar|ver|hacer"
                 r"|trabajar|concentrarme|enfocarme|ponerme)\b"
                 r"|\b(?:estudiemos|repasemos|trabajemos)\b|\bsesion\b|\bpomodoro|\bmodo\s+(?:estudio|foco)\b", t) \
            or (hay_tiempo and re.search(r"\b(?:estudiar|estudio|repasar|repaso)\b", t)):
        return {"tipo": "iniciar", "tema": buscar_tema(t, temas), **plan_sesion(t)}
    return None
