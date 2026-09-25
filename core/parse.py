"""Lee los mensajes de los proveedores y los convierte en renglones.

Todas las estructuras que maneja salieron de mensajes reales:
  - bloques por proveedor (una línea con el nombre del proveedor abre el bloque)
  - encabezado con precio + variantes debajo sin precio
  - segundo número entre paréntesis = precio de venta sugerido por el proveedor
  - tablas pegadas con columna de marca
  - líneas de factura del sistema del proveedor (costo = Importe / Cantidad)
  - varios productos con su precio en un mismo renglón
  - precio en la línea de abajo
  - "sin salames" / "no hay X" = faltante
  - "hamburguesas igual" = sin cambio
  - "no modifiqué precios" = el bloque entero queda sin cambios
"""
import io
import re

import pandas as pd

from . import ErrorDeDatos
from .normalize import sin_acentos
from .providers import es_proveedor

# ---------- limpieza ----------
LINEA_WA = re.compile(r"^\s*\[?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})[,]?\s+(\d{1,2}:\d{2})(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?\]?\s*[-–]?\s*([^:]{1,40}):\s*(.*)$", re.I)
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\u2190-\u21FF\u2B00-\u2BFF]+")
RUIDO = re.compile(r"(cifrado de extremo a extremo|multimedia omitid|se elimin[oó] este mensaje|"
                   r"imagen omitida|audio omitido|sticker omitido)", re.I)
SALUDO = re.compile(r"^\s*(hola|buen d[ií]a|buenas|holis|gracias|saludos|abrazo|beso|"
                    r"c[oó]mo (est[aá]s|andas)|qu[eé] tal)\b.{0,60}$", re.I)
RELATO = re.compile(r"^(van? los productos|te paso|paso la lista|aca va|ah[ií] va|lista de|"
                    r"precios? (para|del)|para el (pr[oó]ximo )?ciclo)\b", re.I)
ETIQUETA = re.compile(r"^\s*(sabores?|variedades?|gustos?|opciones?|rellenos?|sabor|precios?)\s*:?\s*$", re.I)
INTERJ = re.compile(r"\b(uu+h+|bue+no?|che|ah+|eh+|mmm+|jaja\w*|uf+|ay+)\b", re.I)
COLA = re.compile(r"[\s,]*\b(y|e|o|los?|las?|el|la|de|del)\s*$", re.I)
VINETA = re.compile(r"^\s*[\*\-–—•·>]+\s*")
# Ruido del armado del bulto en listas mayoristas: "(12 UxB)", "24 (UxB)".
# Describe cómo viene la caja, no el producto que se vende.
BULTO = re.compile(r"\(?\s*\d{0,4}\s*(?:u\s*x\s*b|ux b|unidades? por bulto)\s*\)?", re.I)
# "si ponen stock, no le des importancia"
STOCK = re.compile(r"\b(sin\s+l[ií]m[ií]te\s+de\s+stock|sin\s+limte\s+de\s+stock|"
                   r"con\s+stock|hay\s+stock|stock\s+(disponible|permanente))\b", re.I)
# marcadores de sección: cortan la herencia del encabezado con precio
SECCION = re.compile(r"^\s*(novedad|nuevo|nueva|atenci[oó]n|importante|oferta|promo)\b", re.I)
# aclaraciones al final de una lista, no son productos
PIE = re.compile(r"^\s*(precios?\s+por\s+kilo|envasado|los?\s+precios?\s+son|"
                 r"hechos?\s+con|elaborad|sin\s+conservantes)\b", re.I)

# ---------- semántica ----------
SIN_CAMBIOS = re.compile(r"\bno (modifiqu[eé]|cambi[eé]|toqu[eé]|actualic[eé])\b.{0,40}\bprecios?\b|"
                         r"\bprecios? (siguen? igual|sin cambios?|se mantienen?)\b|"
                         r"\bquedan? igual\b", re.I)
IGUAL = re.compile(r"\b(igual(es)?|sin cambios?|se mantienen?|mismo precio|"
                   r"como (siempre|el mes pasado))\s*[)\].]*\s*$", re.I)
FALTA = re.compile(r"\b(no hay|no tenemos|no tengo|sin stock|agotad[oa]s?|no va(?:mos)? a (?:haber|tener)|"
                   r"no viene|no llega|suspendid[oa]|no disponible|discontinuad[oa]|fuera de stock|"
                   r"no producimos|pausad[oa]|hasta nuevo aviso|no entra|no hacemos|no sale)\b", re.I)
SIN_PRODUCTO = re.compile(r"^\s*sin\s+(?!stock\b)([a-záéíóúñ][\w\sáéíóúñ]{2,40})$", re.I)
VUELVE = re.compile(r"\b(vuelve|ya hay|volvi[oó]|repuesto|de nuevo disponible|hay de nuevo)\b", re.I)
MESES = r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
RELLENO = re.compile(r"\b(este mes|por ahora|hasta nuevo aviso|por el momento|se retoma.*|vuelve.*|"
                     r"en " + MESES + r"|de " + MESES + r"|lo siento|chicos|gente)\b", re.I)

# ---------- precios ----------
NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?"
# "$2700 (4000)" -> costo 2700, precio de venta sugerido 4000
PRECIO_DOBLE = re.compile(rf"\$?\s*({NUM})\s*\(\s*\$?\s*({NUM})\s*\)")
# el $ puede venir pegado al texto: "12 unidades aprox.$7800"
PRECIO = re.compile(rf"(?<!\d)\$\s?({NUM})|(?<![\w,.$])({NUM})\s*(?:\.-|pesos)(?![\w%])")
# un número de 8 cifras o más sin separadores es un teléfono o un CBU, no un precio
TELEFONO = re.compile(r"\d{8,}")
# líneas de contacto: "Pedidos: 3415550000", "WhatsApp 341...", "alias: ..."
CONTACTO = re.compile(r"^\s*(pedidos?|tel[eé]fono|tel|cel(ular)?|whats?app|wsp|contacto|cbu|cvu|alias|"
                      r"e-?mail|mail|instagram|ig|facebook|direcci[oó]n|horarios?)\b\s*[:.]?", re.I)
# renglones que no son productos aunque tengan números: una fecha sola, un teléfono solo,
# condiciones de venta ("COMPRA MÍNIMA $15000. ENVÍO SIN CARGO")
FECHA_SOLA = re.compile(r"^\s*\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4}\s*$")
TELEFONO_SOLO = re.compile(r"^\s*\+?(?=(?:\D*\d){7})[\d\s()\-.]+$")
CONDICIONES = re.compile(r"\b(compras?|pedidos?|montos?)\s+m[ií]nim|\benv[ií]os?\b.{0,20}\b(sin cargo|gratis)\b|"
                         r"^\s*@\w", re.I)
# lo que queda de una descripción que es solo presentación: "x250cm3", "200 cm3 (1/4 kilo)"
PRESENTACION = re.compile(r"\b(\d+([.,/]\d+)?|x|cm3|cc|ml|l|lts?|litros?|kg|kilos?|k|g|gs|grs?|gramos?|u|un|"
                          r"unid(ades)?|aprox)\b", re.I)


def a_numero(t):
    t = t.strip()
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    elif "," in t:
        t = t.replace(",", ".")
    return float(t)


def precios_de(linea):
    """Devuelve (costo, precio_sugerido, span) o (None, None, None)."""
    m = PRECIO_DOBLE.search(linea)
    if m:
        return a_numero(m.group(1)), a_numero(m.group(2)), m.span()
    for m in PRECIO.finditer(linea):
        if not TELEFONO.fullmatch(m.group(1) or m.group(2)):
            return a_numero(m.group(1) or m.group(2)), None, m.span()
    # número suelto al final de línea: precedido de espacio o separador, o directamente
    # pegado al texto ("harina2000", sin espacio antes del precio)
    m = re.search(rf"(?:[\s:\-–=]\s*|(?<=[a-zA-Záéíóúñ]))({NUM})\s*$", linea)
    if m and len(linea[:m.start()].strip()) > 2 and not TELEFONO.fullmatch(m.group(1)):
        return a_numero(m.group(1)), None, m.span()
    return None, None, None


def solo_presentacion(desc):
    """'x250cm3' o '200 cm3 (1/4 kilo)': dice el tamaño pero no qué producto es."""
    t = re.sub(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)", " ", desc)     # "x250cm3" -> "x 250 cm 3"
    t = re.sub(r"\bcm\s+3\b", "cm3", t, flags=re.I)
    resto = PRESENTACION.sub(" ", t)
    return len(re.sub(r"[^a-záéíóúñ]", "", resto.lower())) < 3


def solo_precio(linea):
    """La línea es únicamente un precio (el producto quedó en la línea anterior)."""
    return bool(re.fullmatch(rf"\s*\$?\s*({NUM})\s*", linea))


def partir_multiproducto(linea):
    """'BRAZUELO $16.900  VACIO $17.500 PULPA $19.300' -> tres segmentos producto+precio.
    Solo se parte si hay 2+ precios con texto propio entre ellos."""
    marcas = [m.span() for m in PRECIO.finditer(linea)]
    if len(marcas) < 2:
        return [linea]
    segmentos, desde = [], 0
    for ini, fin in marcas:
        if len(linea[desde:ini].strip()) < 3:      # precio sin producto propio: no partir
            return [linea]
        segmentos.append(linea[desde:fin].strip())
        desde = fin
    resto = linea[desde:].strip()
    if resto and not PIE.match(resto) and len(resto.split()) > 2 and not STOCK.search(resto):
        segmentos[-1] += " " + resto
    return segmentos


def variantes_texto(t):
    """El mismo texto con y sin la cantidad de compra al inicio ('20 merme de higos').
    Se prueban las dos y gana la que matchee mejor: así '1/2 tarta' no se rompe."""
    v = [t]
    sin_cant = re.sub(r"^\s*\d{1,3}\s+(?=[a-zA-Záéíóúñ])", "", t)
    if sin_cant != t:
        v.append(sin_cant)
    return v


# Línea de factura/pedido del sistema del proveedor:
# "01   990 LIBERTADOR. JABON LIQUIDO BO   8   7000.00   0.00   56000.00"
# El costo real NO es el precio unitario: es Importe/Cantidad, ya con el descuento aplicado.
LINEA_FACTURA = re.compile(
    r"^\s*(\d{1,3})\s+(\d{2,6})\s+(.+?)\s+(\d{1,4})\s+"
    r"([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$")


def fila_de_factura(linea):
    """Devuelve (descripcion, costo_unitario_real) o None."""
    m = LINEA_FACTURA.match(linea)
    if not m:
        return None
    desc, cant, unit, importe = m.group(3), m.group(4), m.group(5), m.group(7)
    try:
        cant, unit, importe = float(cant), float(unit.replace(",", "")), float(importe.replace(",", ""))
    except ValueError:
        return None
    if cant <= 0:
        return None
    real = round(importe / cant, 2)
    # coherencia: el importe tiene que ser plausible contra el precio de lista
    if not (unit * 0.4 <= real <= unit * 1.05):
        return None
    return desc.strip(" ."), real


def fila_de_tabla(linea, idx):
    """Fila de una tabla pegada (celdas separadas por tabulación).

    Devuelve (texto_con_precio, marca) o None. La marca de la fila pisa al proveedor
    del bloque: un almacén manda una tabla, pero en Odoo esos productos cuelgan de
    la marca de cada fila.
    """
    if "\t" not in linea:
        return None
    celdas = [c.strip() for c in linea.split("\t") if c.strip()]
    if len(celdas) < 2:
        return None
    con_precio = [c for c in celdas if precios_de(c)[0] is not None and not es_proveedor(c, idx)]
    if not con_precio:
        return None                      # encabezado de la tabla, no una fila de producto
    precio = con_precio[-1]
    resto = [c for c in celdas if c is not precio]
    marca = next((es_proveedor(c, idx) for c in resto[1:] if es_proveedor(c, idx)), "")
    desc = resto[0] if resto else ""
    return (f"{desc} {precio}".strip(), marca)


# ---------- lectura en bloques ----------
def leer_bloques(texto, idx, inicial=""):
    """[(proveedor, [(texto, indentacion, marca), ...]), ...]

    inicial: proveedor con el que arranca el texto (un mensaje suelto o una fila de la
    planilla ya sabe de quién es; un texto pegado con varios proveedores no)."""
    bloques, actual, lineas = [], inicial, []
    for cruda in texto.splitlines():
        if RUIDO.search(cruda):
            continue

        # línea de factura del proveedor: el costo real sale de Importe/Cantidad
        fac = fila_de_factura(cruda)
        if fac:
            lineas.append((f"{fac[0]} ${fac[1]}", 0, ""))
            continue

        # fila de tabla: la marca de la fila manda sobre el proveedor del bloque
        fila = fila_de_tabla(cruda, idx)
        if fila:
            lineas.append((fila[0], 0, fila[1]))
            continue
        m = LINEA_WA.match(cruda)
        remitente = m.group(3).strip() if m else None
        cuerpo = m.group(4) if m else cruda
        indent = len(cuerpo) - len(cuerpo.lstrip())
        cuerpo = EMOJI.sub(" ", cuerpo)
        cuerpo = VINETA.sub("", cuerpo)
        cuerpo = STOCK.sub(" ", cuerpo)           # "SIN LIMITE DE STOCK" no aporta
        cuerpo = BULTO.sub(" ", cuerpo).strip()   # "(12 UxB)" describe la caja, no el producto
        cuerpo = re.sub(r"\s{2,}", " ", cuerpo)
        if not cuerpo or CONTACTO.match(cuerpo):      # "Pedidos: 3415550000" no es un producto
            continue
        if FECHA_SOLA.match(cuerpo) or TELEFONO_SOLO.match(cuerpo) or CONDICIONES.search(cuerpo):
            continue

        # precio suelto: el producto quedó en la línea de arriba
        if solo_precio(cuerpo) and lineas:
            previo, ind_previo, marca_previa = lineas[-1]
            if precios_de(previo)[0] is None:
                lineas[-1] = (f"{previo} {cuerpo.strip()}", ind_previo, marca_previa)
                continue
        if remitente:
            p = es_proveedor(remitente, idx)
            if p and p != actual:
                if lineas:
                    bloques.append((actual, lineas))
                actual, lineas = p, []

        tiene_precio = precios_de(cuerpo)[0] is not None

        # etiquetas y relato se descartan ANTES de buscar proveedor
        # ("Sabores" se parecía demasiado al proveedor "Sabores de TraVajadoras")
        if not tiene_precio and (ETIQUETA.match(cuerpo) or RELATO.match(cuerpo)):
            continue
        if not tiene_precio and SALUDO.match(cuerpo):
            continue

        # encabezado de bloque: línea corta sin precio que nombra a un proveedor
        if not tiene_precio and len(re.sub(r"\(.*?\)", "", cuerpo).split()) <= 12:
            p = es_proveedor(cuerpo, idx, corte=92)
            if p:
                if lineas:
                    bloques.append((actual, lineas))
                actual, lineas = p, []
                continue

        # línea con precio que arranca con saludo: cortar hasta el último ! o : previo
        if tiene_precio and SALUDO.match(cuerpo):
            ini = precios_de(cuerpo)[2][0]
            corte = max(cuerpo.rfind(c, 0, ini) for c in "!:¡")
            if corte > 0:
                cuerpo = cuerpo[corte + 1:]
        if tiene_precio:
            cuerpo = INTERJ.sub(" ", cuerpo)
            cuerpo = re.sub(r"\s{2,}", " ", cuerpo).strip()
        # varios productos con su precio en un mismo renglón
        for seg in partir_multiproducto(cuerpo):
            lineas.append((seg, indent, ""))
    if lineas:
        bloques.append((actual, lineas))
    return bloques


# ---------- planilla de seguimiento ----------
SI = ("TRUE", "VERDADERO", "SI", "SÍ", "1", "X")


def leer_filas_planilla(fuente):
    """Planilla de seguimiento (Proveedor, Chequeado, Mensaje[, Actualizado]) fila por fila.

    Devuelve [{"proveedor", "chequeado", "mensaje", "actualizado"}]. "actualizado" es
    opcional: si la planilla tiene una columna de fecha, se muestra.
    """
    df, cols = _planilla_df(fuente)
    c_prov, c_chk, c_msg, c_fecha = cols
    filas = []
    for _, d in df.iterrows():
        prov = str(d[c_prov]).strip()
        if not prov:
            continue
        # sin columna Chequeado: None (no se sabe si "sin mensaje" es que mantiene precios)
        filas.append({"proveedor": prov,
                      "chequeado": str(d[c_chk]).strip().upper() in SI if c_chk else None,
                      "mensaje": str(d[c_msg]).strip(),
                      "actualizado": str(d[c_fecha]).strip() if c_fecha else ""})
    return filas


def _planilla_df(fuente):
    try:
        if isinstance(fuente, (bytes, bytearray)):
            df = pd.read_csv(io.BytesIO(fuente), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        else:
            df = pd.read_csv(fuente, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as e:
        raise ErrorDeDatos(f"No pude leer la planilla de mensajes ({e}).")
    norm = {c: sin_acentos(str(c)).lower() for c in df.columns}
    def col(*frags):
        return next((c for c, n in norm.items() if any(f in n for f in frags)), None)
    c_prov, c_chk, c_msg = col("proveedor"), col("chequeado"), col("mensaje")
    c_fecha = col("actualiz", "fecha", "modific")
    if not (c_prov and c_msg):
        raise ErrorDeDatos("La planilla de mensajes tiene que tener las columnas "
                           "Proveedor, Chequeado y Mensaje. "
                           f"Encontré: {', '.join(map(str, df.columns))}.")
    return df, (c_prov, c_chk, c_msg, c_fecha)


def leer_planilla(fuente):
    """Planilla de seguimiento (Proveedor, Chequeado, Mensaje) -> texto en bloques.

    fuente: ruta o bytes del CSV que baja Google Sheets.
    Devuelve (texto, sin_cambios, pendientes):
      - chequeado con mensaje -> el mensaje se analiza
      - chequeado sin mensaje -> mantiene los mismos precios
      - sin chequear          -> pendiente de confirmar
    """
    partes, sin_cambios, pendientes = [], [], []
    for f in leer_filas_planilla(fuente):
        if f["chequeado"] is False:
            pendientes.append(f["proveedor"])
        elif not f["mensaje"]:
            sin_cambios.append(f["proveedor"])      # chequeado sin mensaje = mismos precios
        else:
            partes.append(f"{f['proveedor']}\n{f['mensaje']}\n")
    return "\n".join(partes), sin_cambios, pendientes


def huella(*partes):
    """Identifica un texto sin importar espacios ni mayúsculas: si el proveedor no tocó
    su mensaje, la huella es la misma y no es un precio nuevo."""
    import hashlib
    t = " ".join(re.sub(r"\s+", " ", str(p)).strip().lower() for p in partes)
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


def es_planilla(nombre, contenido):
    """¿Es un CSV de planilla de seguimiento o un texto suelto?"""
    if not str(nombre).lower().endswith(".csv"):
        return False
    primera = contenido[:500].decode("utf-8-sig", errors="ignore").splitlines()[:1]
    cab = sin_acentos(primera[0]).lower() if primera else ""
    return "proveedor" in cab and "mensaje" in cab


# ---------- renglones ----------
# frases informativas: no son variantes de un producto ("Consultar productos en envase retornable.")
INFORMATIVO = re.compile(r"^\s*(consult\w*|ped\w+|avis\w*|hacemos|enviamos|entregamos|record\w*|"
                         r"tambi[eé]n|tenemos|trabajamos|cualquier|por favor|gracias|abrazo|saludos)\b", re.I)


def limpiar_desc(texto, span):
    """La descripción de un renglón: sin el precio, sin artículos al principio ni conectores al final."""
    d = (texto[:span[0]] + " " + texto[span[1]:]).strip(" .:-–=$()") if span else texto
    d = re.sub(r"^\s*(las?|los?|el|la)\s+", "", d, flags=re.I)
    for _ in range(3):
        d = COLA.sub("", d).strip(" .,:;-–—=")
    return d


def extraer_renglones(bloques):
    """Convierte los bloques en renglones: costo, faltante o sin cambio.

    Devuelve (renglones, notas).
    """
    filas, notas = [], []
    for proveedor, lineas in bloques:
        if any(SIN_CAMBIOS.search(t) for t, _, _ in lineas):
            notas.append({"proveedor": proveedor or "(sin identificar)",
                          "nota": "avisó que no modifica precios este ciclo",
                          "linea": next(t for t, _, _ in lineas if SIN_CAMBIOS.search(t))})
            continue

        pendiente = None   # encabezado con precio esperando variantes
        titulo = None      # título sin precio ("Miel en envases de plástico.") para los renglones de abajo
        for i, (texto, indent, marca) in enumerate(lineas):
            prov_fila = marca or proveedor
            costo, sugerido, span = precios_de(texto)
            base_desc = limpiar_desc(texto, span)

            # un título de sección o un pie de lista cortan la herencia del encabezado
            if costo is None and (SECCION.match(texto) or PIE.match(texto)):
                pendiente = titulo = None
                continue

            # b) título: sin precio, y abajo vienen renglones que solo dicen tamaño y precio
            #    "Miel en envases de plástico."  /  "x250cm3 $3200"  /  "x360cm3 $4500"
            if costo is None and i + 1 < len(lineas) and not FALTA.search(texto) and not IGUAL.search(texto):
                sig = lineas[i + 1][0]
                c_sig, _, s_sig = precios_de(sig)
                if c_sig is not None and solo_presentacion(limpiar_desc(sig, s_sig)):
                    titulo = base_desc
                    pendiente = None
                    continue

            # a) variante: sin precio, debajo de un encabezado con precio
            if costo is None and pendiente and len(texto.split()) <= 10 and not INFORMATIVO.match(texto) \
               and not FALTA.search(texto) and not SIN_PRODUCTO.match(texto) and not IGUAL.search(texto):
                filas.append({**pendiente["datos"], "descripcion": f"{pendiente['desc']} {texto}",
                              "linea": f"{pendiente['linea']}  →  {texto}", "tipo": "costo"})
                continue

            if costo is not None:
                bajo_titulo = bool(titulo) and solo_presentacion(base_desc)
                if bajo_titulo:
                    linea = f"{titulo}  →  {texto}"
                    base_desc = f"{titulo} {base_desc}"
                else:
                    linea, titulo = texto, None      # un producto con nombre propio cierra el título
                # un renglón que es solo un tamaño no tiene variantes debajo
                pendiente = None if bajo_titulo else {
                    "indent": indent, "desc": base_desc, "linea": texto,
                    "datos": {"proveedor": prov_fila, "proveedor_bloque": proveedor,
                              "costo_nuevo": costo, "precio_sugerido": sugerido}}
                filas.append({"proveedor": prov_fila, "proveedor_bloque": proveedor,
                              "descripcion": base_desc, "costo_nuevo": costo,
                              "precio_sugerido": sugerido, "linea": linea, "tipo": "costo",
                              "provisorio": True})
                continue

            pendiente = None
            m = SIN_PRODUCTO.match(texto)
            if (FALTA.search(texto) and not VUELVE.search(texto)) or m:
                desc = m.group(1) if m else RELLENO.sub(" ", FALTA.sub(" ", texto))
                mes = re.search(MESES, texto, re.I)
                filas.append({"proveedor": prov_fila, "proveedor_bloque": proveedor,
                              "descripcion": desc.strip(" .,:;-–"),
                              "linea": texto, "tipo": "faltante",
                              "mes": mes.group(0).lower() if mes else ""})
            elif IGUAL.search(texto):
                filas.append({"proveedor": prov_fila, "proveedor_bloque": proveedor,
                              "descripcion": IGUAL.sub("", texto).strip(" .,:;-–()"),
                              "linea": texto, "tipo": "sin cambio"})
            elif re.search(r"\d", texto) and not INFORMATIVO.match(texto):
                # tiene números (probablemente un intento de precio) pero no matcheó ningún
                # caso conocido: se avisa en vez de perderla en silencio
                notas.append({"proveedor": prov_fila or "(sin identificar)",
                              "nota": "no encontré un precio en esta línea", "linea": texto})

    # un encabezado con variantes no es un producto en sí mismo
    con_variantes = {f["linea"].split("  →  ")[0] for f in filas if "  →  " in f.get("linea", "")}
    filas = [f for f in filas if not (f.get("provisorio") and f["linea"] in con_variantes)]
    for f in filas:
        f.pop("provisorio", None)
    return filas, notas
