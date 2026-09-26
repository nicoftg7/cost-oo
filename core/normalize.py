"""Normalización de texto: unidades, abreviaturas, plurales.

Se aplica igual al catálogo de Odoo y a los mensajes, así los dos lados caen en la
misma forma canónica ("750 g" == "750g" == "0,75 kg").
"""
import re
import unicodedata
from functools import lru_cache

# Equivalencias de unidades para normalizar "750 g" == "750g" == "0,75 kg"
UNID = [
    (r"\b(\d+(?:[.,]\d+)?)\s*(kilos?|kgs?|kg)\b",  lambda m: f"{float(m.group(1).replace(',','.'))*1000:g}g"),
    (r"\b(\d+(?:[.,]\d+)?)\s*(gramos?|grs?|gr|gs|g)\b", lambda m: f"{float(m.group(1).replace(',','.')):g}g"),
    (r"\b(\d+(?:[.,]\d+)?)\s*(litros?|lts?|lt|l)\b", lambda m: f"{float(m.group(1).replace(',','.'))*1000:g}cc"),
    (r"\b(\d+(?:[.,]\d+)?)\s*(cc|ml|mililitros?)\b", lambda m: f"{float(m.group(1).replace(',','.')):g}cc"),
    (r"\b(\d+)\s*(unidades?|unid\.?|un|u|uds?)\b",   lambda m: f"{m.group(1)}u"),
]

STOP = {"de", "del", "la", "el", "los", "las", "con", "y", "en", "por", "sin", "a", "para", "x"}

# Abreviaturas y jerga que sirven para cualquier negocio de Argentina. Las de cada
# negocio (una marca recortada por su distribuidora, cómo le dice un productor a algo)
# van en la memoria, en abreviaturas.csv, y se suman con cargar_abreviaturas().
ABREV = {
    # listas de distribuidoras
    r"\bac\b": "aceite", r"\bac[e]?t\b": "aceite", r"\bhar\b": "harina",
    r"\bfid\b": "fideos", r"\bgall?\b": "galletitas", r"\bgalls?\b": "galletitas",
    r"\bcons\b": "conserva", r"\byerb\b": "yerba", r"\bazuc\b": "azucar",
    r"\bleg\b": "legumbres", r"\bmerm\b": "mermelada", r"\bint\b": "integral",
    r"\bextra v\.?\b": "extra virgen", r"\bv\.?\s*extra\b": "extra virgen",
    r"\bnat\b": "natural", r"\bdesc\b": "descremado", r"\bent\b": "entero",
    r"\bch(oc)?\b": "chocolate", r"\bdulc?\b": "dulce", r"\bpque?t\b": "paquete",
    r"\bs/\s*": "sin ", r"\bc/\s*": "con ",
    # descripciones recortadas a 30 caracteres
    r"\bdesme\b": "desmenuzado", r"\bdesmenuz\b": "desmenuzado", r"\baceit\b": "aceite",
    r"\bnatur\b": "natural", r"\bchampig?n?\b": "champignon", r"\bfrutill\b": "frutilla",
    r"\brodaj\b": "rodajas", r"\bdeshues\.?\b": "deshuesado",
    # jerga y errores de tipeo comunes en mensajes
    r"\bpasta\s+frola\b": "pastafrola", r"\bchoris?\b": "chorizos",
    r"\broque(fort)?\b": "queso azul", r"\bmerme(lada)?s?\b": "mermelada",
    r"\bpimi\.?\b": "pimienta", r"\bn\.?\s*granos?\b": "negra granos",
    r"\brayado\b": "rallado", r"\bpre\s+pizza\b": "prepizza", r"\bespecies\b": "especias",
    r"\bmieles\b": "miel", r"\byogur\b": "yogurt", r"\bdonitas?\b": "donas",
    r"\bnapoletana\b": "napolitano",
}

# las del negocio, cargadas desde la memoria
_PROPIAS = {}


def patron_de(texto):
    """'bahi' -> patrón que reemplaza la palabra entera 'bahi' (sin tocar 'bahia')."""
    return rf"\b{re.escape(sin_acentos(str(texto)).lower().strip())}\b"


def cargar_abreviaturas(pares):
    """pares: [(como lo escriben, qué significa)]. Si cambiaron, se reemplazan las propias
    y se vacía el caché de expandir()."""
    nuevas = {patron_de(t): sin_acentos(str(s)).lower().strip()
              for t, s in pares if str(t).strip()}
    if nuevas != _PROPIAS:
        _PROPIAS.clear()
        _PROPIAS.update(nuevas)
        expandir.cache_clear()

TAMANO = re.compile(r"\b(\d+(?:\.\d+)?)(g|cc|u)\b")


def sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=100_000)
def expandir(s):
    """Desabrevia texto de proveedor antes de normalizar (no se aplica al catálogo)."""
    t = sin_acentos(str(s)).lower()
    for pat, rep in ABREV.items():
        t = re.sub(pat, rep, t)
    for pat, rep in _PROPIAS.items():
        t = re.sub(pat, rep, t)
    return t


@lru_cache(maxsize=100_000)
def normalizar(s):
    """Clave canónica: sin acentos, minúsculas, unidades unificadas, sin stopwords.
    Se cachea: una lista de mil renglones compara los mismos nombres miles de veces."""
    s = sin_acentos(str(s)).lower()
    s = s.replace("½", "1/2").replace("¼", "1/4").replace("×", "x").replace("·", "x")
    # "24 x 300 g" o "10x400g" = bulto de N unidades de M gramos. A Odoo le importa
    # la unidad, así que nos quedamos con el gramaje y descartamos el conteo del bulto.
    s = re.sub(r"\b\d{1,3}\s*x\s*(\d+(?:[.,]\d+)?)\s*(gramos?|grs?|gr|gs|g|kg|cc|ml|l)\b",
               r"\1 \2", s)
    # fracciones de kilo antes que nada: "1/2 kg" es 500 g, no "2 kg"
    s = re.sub(r"\b1\s*/\s*2\s*(kg|kilos?)\b", "500 g", s)
    s = re.sub(r"\b1\s*/\s*4\s*(kg|kilos?)\b", "250 g", s)
    s = re.sub(r"\b3\s*/\s*4\s*(kg|kilos?)\b", "750 g", s)
    for pat, rep in UNID:
        s = re.sub(pat, rep, s)
    # "x N" sin unidad detrás: hasta 48 es cantidad de unidades ("x 12" huevos),
    # de 100 para arriba es gramaje ("reggianito x 500" son 500 g, no 500 potes).
    # En el medio queda el número pelado, que no arriesga un desajuste falso.
    s = re.sub(r"\bx\s*(\d{1,2})(?![\w.,])(?!\s*(?:g|gs|gr|grs|gramos?|kg|kilos?|cc|ml|l|lt|litros?)\b)",
               lambda m: f"{m.group(1)}u" if int(m.group(1)) <= 48 else m.group(1), s)
    s = re.sub(r"\bx\s*(\d{3,})(?![\w.,])(?!\s*(?:g|gs|gr|grs|gramos?|kg|kilos?|cc|ml|l|lt|litros?)\b)",
               r"\1g", s)
    s = re.sub(r"[^a-z0-9/ ]+", " ", s)
    # singular/plural: se aplica igual al catálogo y al mensaje, así "salames"
    # y "salame" caen en la misma forma aunque no sea el singular correcto
    toks = [t[:-1] if (len(t) > 4 and t.endswith("s") and not t[-2].isdigit()) else t
            for t in s.split() if t not in STOP]
    return " ".join(toks)


def tamanos(texto, de_proveedor=True):
    """Presentaciones que nombra un texto: {('25', 'g'), ('12', 'u')}."""
    t = expandir(texto) if de_proveedor else texto
    return set(TAMANO.findall(normalizar(t)))


def peso_kg(nombre):
    """Peso del producto a partir de su nombre en Odoo: '150 g' -> 0.15"""
    for valor, unidad in TAMANO.findall(normalizar(nombre)):
        if unidad == "g":
            return float(valor) / 1000
    return None


def desajuste_tamano(texto_prov, nombre_odoo):
    """Detecta que el proveedor cotiza otra presentación (25 g contra 50 g cargados)."""
    a = tamanos(texto_prov)
    b = tamanos(nombre_odoo, de_proveedor=False)
    if not a or not b or a & b:
        return ""
    fmt = lambda s: ", ".join(f"{v}{u}" for v, u in sorted(s))
    return f"presentación distinta: proveedor {fmt(a)} / Odoo {fmt(b)}"
