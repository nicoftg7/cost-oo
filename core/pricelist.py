"""Listas de precios de proveedores: PDF, Excel, CSV o foto.

leer_fuente() devuelve una Lectura: artículos con código (lo mejor: el código se aprende
y el mes siguiente entra solo) o, si la lista no tiene códigos, su texto para leerlo como
un mensaje de ese proveedor. También la fecha que dice la lista, para avisar si es vieja.

Formatos que aparecieron hasta ahora (cada uno con su prueba):
  - tabla con encabezados, varios productos compartiendo un precio en celdas combinadas,
    y columnas de precio de bulto, costo unitario y precio sugerido (se toma el costo)
  - una columna, con marca y precio con $:      80018 Aceite ZANONI 900cc ZANONI $ 2.598 0
    (a veces el PDF parte el número: "$ 1 4.294" es 14.294), o códigos cortos: 1 TAPAS $895,00
  - dos columnas de artículos por página:        AL-0014 ATUN BAHIA LOMITO 170GR 2670.631 48
  - lista, descuento y neto:                     01-0112 Atún ... 2,226.21 18.78% 1,808.13
  - anterior, aumento y final:                   300 BONDIOLA 20472 7% 21905
  - sin códigos (un mensaje en PDF) o una foto:  se lee como mensaje
"""
import io
import re
from dataclasses import dataclass, field

import pandas as pd

from . import ErrorDeDatos
from .normalize import normalizar, sin_acentos
from .providers import es_proveedor

CODIGO = r"(?:[A-Za-z0-9]{1,4}-\d{3,6}|\d{3,7})"      # "O4-2001": a veces tipean la O por el 0
# con $ el precio es inequívoco, así que el código puede ser corto: "1 TAPAS P/TORTAS FRITAS $895,00"
CODIGO_CORTO = r"(?:[A-Za-z0-9]{1,4}-\d{3,6}|\d{1,7})"
PRECIO_TXT = r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}"
NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
# 7795933000502 01-0112 Atún Lomitos al Natural (Ecuador) 88 48 x 170 grs. 2,226.21 18.78% [5.00%] 1,808.13
# (código de barras opcional, precio de lista, uno o más descuentos, precio NETO al final)
CON_DESCUENTO = re.compile(
    rf"^\s*(?:\d{{8,14}}\s+)?({CODIGO})\s+(.+?)\s+({PRECIO_TXT})\s+(\d+(?:[.,]\d+)?%)"
    rf"(?:\s+\d+(?:[.,]\d+)?%?)*\s+({PRECIO_TXT})\s*$")
# 300 BONDIOLA 20472 7% 21905   (precio anterior, porcentaje, precio final; sin decimales)
CON_PORCENTAJE = re.compile(rf"^\s*({CODIGO})\s+(.+?)\s+\$?\s*({NUM})\s+([+-]?\d+(?:[.,]\d+)?)\s*%\s+\$?\s*({NUM})\s*$")
SIN_STOCK = re.compile(r"\b(?:SIN\s+STOCK|S/\s*STOCK)\b", re.I)
# "88 48 x 170 grs.": el 88 es cuántos bultos entran en un pallet, no es parte del producto
PALLET = re.compile(r"\s\d{2,3}\s+(?=\d+\s*x\s*\d)")
# 80018 Aceite ZANONI ... ZANONI $ 2.598 0
CON_PESOS = re.compile(rf"^\s*({CODIGO_CORTO})\s+([^$]+?)\s+\$\s*([\d.,]+(?:\s+[\d.,]+){{0,2}})\s*$")
# AL-0014 ATUN BAHIA LOMITO NATUR 170GR PR 2670.631 48 NUEVO
# después del precio puede venir la cantidad por bulto ("48", "30X48", "12/24", "576(2", "12U")
# y alguna anotación ("NUEVO"), o nada ("46508 MAQ AFEITAR ... 48X6 882.858")
CON_DECIMALES = re.compile(rf"^\s*({CODIGO})\s+(.+?)\s+(\d[\d.,]*[.,]\d{{2,3}})(?:\s+(\d\S*))?(?:\s+[A-Za-z/].*)?\s*$")
SECCIONES_GENERICAS = {"vario", "varios", "otro", "otros", "promo", "promos", "oferta", "ofertas",
                       "nuevo", "nuevos", "general", "linea", "almacen", "bebida", "bebidas"}
# un renglón que parece tener precio pero no se pudo leer entero (se avisa, no se pierde callado)
PARECE_ARTICULO = re.compile(r"[A-Za-z]{3,}.*\d+[.,]\d{2,3}\b")
# un texto sin códigos igual sirve si tiene renglones con precio
PRECIO_EN_TEXTO = re.compile(r"\$\s*\d")

# anotaciones de la distribuidora que no son parte del producto
RUIDO_LISTA = [
    (re.compile(r"\b(?:PR|OF)\b(?:\s*\d{1,2}/\d{1,2})?", re.I), " "),   # "PR 14/9" = precio/oferta desde
    (re.compile(r"^\s*Z{3}\s*", re.I), ""),                              # "ZZZ" = discontinuado
    (re.compile(r"\b(?:NUEVO|SUBE|OFERTA?)\b\s*$", re.I), ""),
    (re.compile(r"\(\s*\d*\s*U\s*x\s*B\s*\)|\b\d+\s*\(\s*UxB\s*\)", re.I), " "),  # "(12 UxB)"
]

IMAGENES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


@dataclass
class Lectura:
    articulos: list = field(default_factory=list)   # con código y precio
    no_leidos: list = field(default_factory=list)   # renglones con precio que no se pudieron leer enteros
    texto: str = ""                                 # si no hay artículos: se lee como mensaje
    fecha: object = None                            # la fecha que dice la lista (datetime.date)
    de_foto: bool = False                           # el texto salió de una imagen


def limpiar_descripcion(d):
    for pat, rep in RUIDO_LISTA:
        d = pat.sub(rep, d)
    return re.sub(r"\s{2,}", " ", d).strip(" -.")


def limpiar_linea_pdf(l):
    """Arreglos de cómo algunos PDF guardan el texto:
    "S n a c k s" (letras espaciadas) -> "Snacks"; "verdu R ra" (una letra de un sello
    metida en el medio) -> "verdura"."""
    l = re.sub(r"\b(?:[A-Za-zÁÉÍÓÚáéíóúÑñ] ){2,}[A-Za-zÁÉÍÓÚáéíóúÑñ]\b",
               lambda m: m.group(0).replace(" ", ""), l)
    l = re.sub(r"([a-záéíóúñ]) [A-Z] ([a-záéíóúñ])", r"\1\2", l)
    return l


# ---------- números ----------
def separador_decimal(crudos):
    """Mira todos los precios de la lista juntos para decidir qué es el punto.
    '2670.631' solo puede ser decimal (un separador de miles nunca tiene 4 cifras
    antes); '2.598' y '14.294' solos son miles."""
    for t in crudos:
        t = t.replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", t):       # 2,226.21
            return "."
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", t):       # 2.226,21
            return ","
        if re.fullmatch(r"\d{4,}\.\d+", t) or re.fullmatch(r"\d+\.\d{1,2}", t):
            return "."
        if re.search(r",\d{1,2}$", t):
            return ","
    return ","


def a_numero(t, decimal):
    t = t.replace(" ", "").replace("$", "")
    if decimal == ".":
        t = t.replace(",", "")
    else:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def unir_digitos_partidos(t):
    """'1 4.294' -> '14.294' (el PDF separó el primer dígito); '2.598 0' -> '2.598'
    (el 0 es otra columna)."""
    partes = t.split()
    if len(partes) >= 2 and re.fullmatch(r"\d{1,3}", partes[0]) and re.fullmatch(r"\d{1,3}(?:[.,]\d+)+", partes[1]):
        return partes[0] + partes[1]
    return partes[0]


def precio_de_celda(t):
    """'$1 23.870,97' o '$ 1 .548,39' (el PDF metió espacios) -> '123.870,97' / '1.548,39'.
    Si la celda tiene más de un número (p. ej. "x 12 un. $723,00", una tabla sin columnas
    reales donde toda la fila cayó en una sola celda), el que tiene "$" adelante es el
    precio; un número suelto sin "$" solo se usa si no hay ningún otro."""
    t = str(t or "").replace("\n", " ")
    m = re.search(r"\$\s*([\d][\d\s.,]*\d)", t) or re.search(r"([\d][\d\s.,]*\d)", t)
    return re.sub(r"\s+", "", m.group(1)) if m else ""


# ---------- fechas ----------
FECHA = re.compile(r"\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\b")


def fecha_de(texto):
    """La fecha más reciente y creíble que aparece en la lista (o None)."""
    import datetime as dt
    hoy = dt.date.today()
    fechas = []
    for d, m, a in FECHA.findall(texto or ""):
        a = int(a) + (2000 if len(a) == 2 else 0)
        try:
            f = dt.date(a, int(m), int(d))
        except ValueError:
            continue
        if dt.date(2000, 1, 1) <= f <= hoy + dt.timedelta(days=60):
            fechas.append(f)
    return max(fechas) if fechas else None


# ---------- PDF ----------
# para detectar columnas solo cuentan códigos "de verdad" (AL-0014, 80018), no un
# "500" que está adentro de una descripción, y tampoco un precio: un precio de 5 cifras
# en la mitad derecha de una página de una sola columna se le parece mucho a un código
COD_COLUMNA = re.compile(r"(?:[A-Za-z]{1,4}-\d{3,6}|\d{4,7})$")


def _corte_de_columna(palabras, ancho):
    """Si la página tiene una segunda columna de artículos, la posición x donde empieza (o
    None si no). Un código real de columna tiene más texto a su derecha en el mismo
    renglón (la descripción, después el precio); un precio al final de una línea de una
    sola columna no tiene nada más después, así que no cuenta como candidato."""
    from collections import Counter

    def sigue_texto(w):
        return any(abs(o["top"] - w["top"]) <= 2 and o["x0"] > w["x1"] + 2 for o in palabras)

    xs = [w["x0"] for w in palabras if COD_COLUMNA.match(w["text"]) and sigue_texto(w)]
    izq = [x for x in xs if x < ancho * 0.3]
    der = [x for x in xs if x >= ancho * 0.3]
    if not der:
        return None
    # una segunda columna existe si sus códigos arrancan todos a la misma altura horizontal
    moda, _ = Counter(round(x) for x in der).most_common(1)[0]
    alineados = [x for x in der if abs(x - moda) <= 3]
    if len(izq) >= 3 and len(alineados) >= max(5, 0.3 * len(izq)):
        return min(alineados) - 1.5
    return None


def _paginas_texto(pdf):
    """Texto de cada página. Si la página tiene dos columnas de artículos, las separa."""
    textos = []
    for pg in pdf.pages:
        corte = _corte_de_columna(pg.extract_words(), pg.width)
        if corte is not None:
            a = pg.filter(lambda o: o.get("object_type") != "char" or o["x0"] < corte)
            b = pg.filter(lambda o: o.get("object_type") != "char" or o["x0"] >= corte)
            textos.append((a.extract_text() or "") + "\n" + (b.extract_text() or ""))
        else:
            textos.append(pg.extract_text() or "")
    return textos


# columnas de una tabla, reconocidas por su encabezado
COL_CODIGO = re.compile(r"c[oó]d|\bid\b", re.I)
COL_DESC = re.compile(r"detalle|descrip|producto|art[ií]culo|nombre", re.I)
COL_UNIDADES = re.compile(r"u\s*x\s*b|unid\w*\s*(x|por)\s*bulto|x\s*bulto", re.I)
COL_PRECIO = re.compile(r"precio|costo|unitario|importe|valor|final|neto", re.I)
COL_NO_ES_COSTO = re.compile(r"sugerid|pvp|p\.\s*v\.\s*p|venta al p|p[uú]blico|anterior", re.I)


def _columnas(encabezado):
    """{codigo, descripcion, unidades, precio, precio_es_bulto} a partir de la fila de títulos,
    o None si esa fila no parece un encabezado."""
    textos = [re.sub(r"\s+", " ", str(c or "")) for c in encabezado]
    cols = {}
    for i, t in enumerate(textos):
        if COL_CODIGO.search(t) and "codigo" not in cols:
            cols["codigo"] = i
        elif COL_DESC.search(t) and "descripcion" not in cols:
            cols["descripcion"] = i
        elif COL_UNIDADES.search(t):
            cols["unidades"] = i
    # el costo: primero "costo"/"unitario"; después un precio que no sea de bulto; después el del bulto
    precios = [(i, t) for i, t in enumerate(textos) if COL_PRECIO.search(t) and not COL_NO_ES_COSTO.search(t)]
    orden = sorted(precios, key=lambda p: (0 if re.search(r"costo|unitario", p[1], re.I)
                                          else 2 if re.search(r"bulto|caja", p[1], re.I) else 1))
    if orden:
        cols["precio"] = orden[0][0]
        cols["precio_es_bulto"] = bool(re.search(r"bulto|caja", orden[0][1], re.I)) and "unidades" in cols
    return cols if "precio" in cols and ("codigo" in cols or "descripcion" in cols) else None


def _articulos_de_tablas(pdf, idx):
    """Tablas con bordes: se reconocen las columnas por el encabezado. Soporta celdas
    combinadas (tres productos con un precio en común) y descripciones que el PDF corrió
    a la fila siguiente."""
    filas, crudos = [], []
    for pg in pdf.pages:
        for tabla in pg.extract_tables():
            cols, seccion, pendientes = None, "", []
            for fila in tabla:
                celdas = [c if c is not None else "" for c in fila]
                if cols is None:
                    cols = _columnas(celdas)
                    continue
                llenas = [c for c in celdas if str(c).strip()]
                if len(llenas) == 1 and not PRECIO_EN_TEXTO.search(str(llenas[0])):
                    k = normalizar(llenas[0])          # título de sección: puede ser una marca
                    if idx and k in idx and k not in SECCIONES_GENERICAS:
                        seccion = idx[k]
                    continue
                precio = precio_de_celda(celdas[cols["precio"]]) if cols["precio"] < len(celdas) else ""
                if not precio:
                    continue
                codigos = [c.strip() for c in str(celdas[cols["codigo"]]).split("\n")
                           if c.strip()] if "codigo" in cols else []
                descs = [limpiar_linea_pdf(d.strip()) for d in str(celdas[cols.get("descripcion", 0)]).split("\n")
                         if d.strip()] if "descripcion" in cols else []
                unidades = None
                if cols.get("precio_es_bulto"):
                    m = re.search(r"\d+", str(celdas[cols["unidades"]]))
                    unidades = int(m.group()) if m else None
                # códigos que quedaron de la fila anterior (el PDF corrió las descripciones)
                if not codigos and pendientes:
                    codigos, pendientes = pendientes[:len(descs)], pendientes[len(descs):]
                elif len(codigos) > len(descs) > 0:
                    codigos, pendientes = codigos[:len(descs)], codigos[len(descs):]
                for i, desc in enumerate(descs or [""] * len(codigos)):
                    cod = codigos[i] if i < len(codigos) else ""
                    crudos.append(precio)
                    filas.append({"codigo": cod.upper(), "desc": desc, "precio_txt": precio,
                                  "unidades": unidades, "seccion": seccion,
                                  "linea": f"{cod} {desc} {celdas[cols['precio']]}".replace("\n", " ").strip()})
    if not filas:
        return []
    decimal = separador_decimal(crudos)
    salida = []
    for f in filas:
        precio = a_numero(f["precio_txt"], decimal)
        if precio is None or precio <= 1:
            continue
        if f["unidades"]:
            precio = round(precio / f["unidades"], 2)
        desc, marca = separar_marca(f["desc"], idx)
        salida.append({"codigo": f["codigo"], "descripcion": limpiar_descripcion(desc), "marca": marca,
                       "marca_seccion": f["seccion"], "precio": precio, "sin_stock": False,
                       "linea": f["linea"]})
    return salida


def _leer_pdf(contenido, idx):
    try:
        import pdfplumber
    except ImportError:
        raise ErrorDeDatos("Para leer listas en PDF falta instalar pdfplumber "
                           "(se instala solo al abrir la app con el .bat).")
    try:
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            texto_crudo = "\n".join(_paginas_texto(pdf))
            lineas = [limpiar_linea_pdf(l) for l in texto_crudo.splitlines()]
            if len(re.sub(r"\s", "", texto_crudo)) < 40:
                # casi sin texto: es un escaneo; se lee la imagen de cada página
                from .ocr import texto_de_pdf_escaneado
                texto = texto_de_pdf_escaneado(pdf)
                return Lectura(texto=texto, fecha=fecha_de(texto), de_foto=True)
            articulos = _articulos_de_tablas(pdf, idx)
    except ErrorDeDatos:
        raise
    except Exception as e:
        raise ErrorDeDatos(f"No pude leer el PDF ({e}).")
    texto = "\n".join(lineas)
    if articulos:
        return Lectura(articulos=articulos, fecha=fecha_de(texto))
    articulos, no_leidos = renglones_de_texto(lineas, idx)
    return Lectura(articulos=articulos, no_leidos=no_leidos, texto=texto, fecha=fecha_de(texto))


def leer_pdf(contenido, idx=None):
    """Compatibilidad: (articulos, no_leidos)."""
    l = _leer_pdf(contenido, idx)
    return l.articulos, l.no_leidos


def renglones_de_texto(lineas, idx=None):
    """Devuelve (filas, renglones_que_no_se_pudieron_leer)."""
    crudos, no_leidos = [], []
    seccion = ""     # marca de la sección: una línea que dice solo "BAHIA" arriba de los artículos
    for l in lineas:
        m = CON_DESCUENTO.match(l)
        if m:
            desc = PALLET.sub(" ", m.group(2))
            crudos.append(("neto", l, m.group(1), desc, m.group(5), seccion, None))
            continue
        m = CON_PORCENTAJE.match(l)
        if m:
            # si el final es mayor, el primero es el precio anterior (una lista de aumentos)
            crudos.append(("porcentaje", l, m.group(1), m.group(2), m.group(5), seccion, m.group(3)))
            continue
        m = CON_PESOS.match(l)
        if m:
            crudos.append(("pesos", l, m.group(1), m.group(2), unir_digitos_partidos(m.group(3)), seccion, None))
            continue
        m = CON_DECIMALES.match(l)
        if m:
            crudos.append(("decimal", l, m.group(1), m.group(2), m.group(3), seccion, None))
        elif PARECE_ARTICULO.search(l) and "*****" not in l:
            no_leidos.append(l.strip())
        elif idx and 0 < len(l.split()) <= 4:
            # solo coincidencia exacta: "BEBIDAS" no puede volverse la marca "Bebidas del Sur",
            # y una palabra genérica ("VARIOS") no es una marca aunque algún producto la use
            k = normalizar(l)
            if k in idx and k not in SECCIONES_GENERICAS:
                seccion = idx[k]
    if not crudos:
        return [], no_leidos
    decimal = separador_decimal([c[4] for c in crudos])
    filas, vistos = [], set()
    for tipo, linea, codigo, desc, precio_txt, seccion, anterior_txt in crudos:
        precio = a_numero(precio_txt, decimal)
        sin_stock = bool(SIN_STOCK.search(desc))
        if precio is None or (precio <= 1 and not sin_stock):   # "BZ-0143 ****** 0.001": relleno
            continue
        if (codigo.upper(), precio) in vistos:     # el PDF repite renglones entre páginas
            continue
        vistos.add((codigo.upper(), precio))
        anterior = a_numero(anterior_txt, decimal) if anterior_txt else None
        desc, marca = separar_marca(SIN_STOCK.sub(" ", desc), idx)
        filas.append({"codigo": codigo.upper(), "descripcion": limpiar_descripcion(desc),
                      "marca": marca, "marca_seccion": seccion, "precio": precio,
                      "precio_anterior": anterior if anterior and anterior < precio else None,
                      "sin_stock": sin_stock, "linea": linea.strip()})
    return _sin_codigos_repetidos(filas), no_leidos


def _sin_codigos_repetidos(filas):
    """Si la lista usa el mismo código para dos productos distintos (pasa), ese código no
    sirve para reconocerlos: esas filas se identifican por la descripción."""
    from collections import defaultdict
    descs = defaultdict(set)
    for f in filas:
        if f["codigo"]:
            descs[f["codigo"]].add(normalizar(f["descripcion"]))
    for f in filas:
        if len(descs.get(f["codigo"], ())) > 1:
            f["codigo_repetido"] = f["codigo"]
            f["codigo"] = ""
    return filas


def separar_marca(desc, idx):
    """'Aceite ZANONI Girasol x 900cc (12 UxB) ZANONI' -> ('Aceite ZANONI Girasol x 900cc (12 UxB)', 'Zanoni')
    Solo si la columna de marca (las últimas palabras, en mayúsculas) es exactamente una marca
    conocida: "RAVIOLES DE JAMON Y QUESO" no puede perder "Y QUESO" por parecerse a algo."""
    if not idx:
        return desc, ""
    palabras = desc.split()
    for k in (3, 2, 1):
        if len(palabras) > k + 1:
            cola = " ".join(palabras[-k:])
            clave = normalizar(cola)
            if cola.isupper() and clave in idx and clave not in SECCIONES_GENERICAS:
                return " ".join(palabras[:-k]), idx[clave]
    return desc, ""


# ---------- Excel / CSV ----------
PISTAS = {
    "codigo":      ("codigo", "cod", "sku", "articulo", "art.", "id"),
    "descripcion": ("descripcion", "detalle", "producto", "nombre", "denominacion", "articulo"),
    "precio":      ("precio", "costo", "importe", "valor", "unitario", "neto", "lista", "pvp"),
    "marca":       ("marca",),
}


def detectar_columnas(df):
    """Encuentra código / descripción / precio / marca aunque la distribuidora los llame distinto."""
    elegidas, usadas = {}, set()
    normal = {c: sin_acentos(str(c)).lower().strip() for c in df.columns}
    for campo in ("precio", "marca", "codigo", "descripcion"):
        for c, n in normal.items():
            if c not in usadas and any(n == p or n.startswith(p) for p in PISTAS[campo]):
                elegidas[campo] = c
                usadas.add(c)
                break
    if "precio" not in elegidas:
        num = {c: pd.to_numeric(df[c], errors="coerce").notna().mean() for c in df.columns if c not in usadas}
        if num and max(num.values()) > 0.6:
            elegidas["precio"] = max(num, key=num.get)
            usadas.add(elegidas["precio"])
    if "descripcion" not in elegidas:
        largo = {c: df[c].astype(str).str.len().mean() for c in df.columns if c not in usadas}
        if largo:
            elegidas["descripcion"] = max(largo, key=largo.get)
    return elegidas


def _campos_por_nombre(etiquetas):
    """Cuántos de código/descripción/precio matchean por el nombre de la columna (sin el
    respaldo numérico de detectar_columnas)."""
    normal = [sin_acentos(str(c)).lower().strip() for c in etiquetas]
    return sum(any(any(n == p or n.startswith(p) for p in pistas) for n in normal)
              for campo, pistas in PISTAS.items() if campo != "marca")


def leer_tabla(nombre, contenido, idx=None):
    try:
        if contenido[:2] == b"PK" or nombre.lower().endswith((".xlsx", ".xls")):   # xlsx es un zip
            df = pd.read_excel(io.BytesIO(contenido), dtype=str)
        else:
            df = pd.read_csv(io.BytesIO(contenido), dtype=str, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as e:
        raise ErrorDeDatos(f"No pude leer la lista {nombre} ({e}).")
    df = df.fillna("")
    # el encabezado real puede haber quedado como una fila de datos más: una fila de
    # título, de logo o de "OBSERVACIONES" arriba de todo hace que pandas tome esa (o una
    # fila vacía) como los nombres de columna. Se busca entre las primeras filas la que
    # más se parezca a un encabezado de verdad, y si hay una mejor que las columnas
    # actuales, se la usa (descartando lo que haya arriba, que tampoco era un producto).
    mejor_fila, mejor_score = None, _campos_por_nombre(df.columns)
    for i in range(min(5, len(df))):
        score = _campos_por_nombre(df.iloc[i])
        if score > mejor_score:
            mejor_fila, mejor_score = i, score
    if mejor_fila is not None:
        df = df.iloc[mejor_fila + 1:].set_axis(list(df.iloc[mejor_fila]), axis=1).reset_index(drop=True)
    cols = detectar_columnas(df)
    if "precio" in cols and not pd.isna(pd.to_numeric(pd.Series([cols["precio"]]), errors="coerce")[0]):
        # la columna de precio se llama "1550": no es un encabezado, es el primer artículo,
        # y pandas se lo comió como si fuera una fila de títulos. Se lo devuelve a los datos.
        primera = pd.DataFrame([list(df.columns)], columns=range(len(df.columns)))
        df = pd.concat([primera, df.set_axis(range(len(df.columns)), axis=1)], ignore_index=True).fillna("")
        cols = detectar_columnas(df)
    if "precio" not in cols:
        raise ErrorDeDatos(f"En la lista {nombre} no encontré la columna de precio. "
                           f"Columnas: {', '.join(map(str, df.columns))}.")
    decimal = separador_decimal(df[cols["precio"]].astype(str).str.replace("$", "").str.strip().tolist())
    filas = []
    for _, r in df.iterrows():
        precio = a_numero(str(r[cols["precio"]]).replace("$", "").strip(), decimal)
        desc = str(r.get(cols.get("descripcion"), "")).strip()
        codigo = str(r.get(cols.get("codigo"), "")).strip() if "codigo" in cols else ""
        if precio is None or precio <= 1 or not (desc or codigo):
            continue
        marca = str(r.get(cols.get("marca"), "")).strip() if "marca" in cols else ""
        if marca and idx:
            marca = es_proveedor(marca, idx, corte=90) or ""
        filas.append({"codigo": codigo.upper(), "descripcion": limpiar_descripcion(desc), "marca": marca,
                      "precio": precio, "linea": " · ".join(str(v) for v in r.values if str(v).strip())})
    return _sin_codigos_repetidos(filas), []


# ---------- entrada única ----------
def es_imagen(nombre, contenido):
    return (nombre.lower().endswith(IMAGENES) or contenido[:3] == b"\xff\xd8\xff"
            or contenido[:8] == b"\x89PNG\r\n\x1a\n" or contenido[:4] == b"RIFF")


def leer_fuente(nombre, contenido, idx=None):
    """PDF, Excel, CSV o foto -> Lectura. Error solo si no hay de dónde sacar precios."""
    if es_imagen(nombre, contenido):
        from .ocr import texto_de_imagen
        texto = texto_de_imagen(contenido)
        lectura = Lectura(texto=texto, fecha=fecha_de(texto), de_foto=True)
    elif contenido[:5] == b"%PDF-" or nombre.lower().endswith(".pdf"):
        lectura = _leer_pdf(contenido, idx)
    else:
        articulos, no_leidos = leer_tabla(nombre, contenido, idx)
        lectura = Lectura(articulos=articulos, no_leidos=no_leidos)
    if not lectura.articulos:
        # sin códigos: si el texto tiene precios, se lee como un mensaje de ese proveedor
        if len(PRECIO_EN_TEXTO.findall(lectura.texto or "")) >= 1:
            lectura.no_leidos = []
            return lectura
        raise ErrorDeDatos(f"No encontré precios en {nombre}."
                           + (" La foto no se lee bien: probá con una más nítida o pasá los precios como mensaje."
                              if lectura.de_foto else ""))
    return lectura


def leer_lista(nombre, contenido, idx=None):
    """Compatibilidad: (articulos, no_leidos); error si no hay artículos con código."""
    l = leer_fuente(nombre, contenido, idx)
    if not l.articulos:
        raise ErrorDeDatos(f"No encontré artículos con código y precio en {nombre}.")
    return l.articulos, l.no_leidos
