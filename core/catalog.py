"""Convierte el export de Odoo en un catálogo consultable."""
import re

import pandas as pd

from . import ErrorDeDatos
from .normalize import normalizar, sin_acentos

# Filas que no son productos comprables en cualquier tienda de Odoo: cupones, descuentos,
# envíos. Las propias de cada negocio ("cuota social") van en no_son_productos.csv.
NO_PRODUCTO = re.compile(
    r"^\s*(\$|\d+\s*%|100%)|"
    r"^(descuento|env[ií]o|tarjeta de regalo|producto gratis|"
    r"dep[oó]sito|anticipo|donaci[oó]n(es)?|prueba)\b", re.I)

# Nombres EXACTOS que acepta la columna de ID externo. Nunca buscarla por fragmento:
# "id" está adentro de "cantIDad a la mano", y tomar esa columna como ID manda
# cantidades de stock al archivo de importación. Pasó de verdad: Odoo las leyó como
# IDs nuevos y creó 15 productos duplicados.
NOMBRES_ID = ("id", "id externo", "external id", "id_externo")

SIN_ID = (
    "Este export de Odoo no tiene la columna de ID externo (\"id\"), así que no sirve "
    "para actualizar costos.\n\n"
    "Volvé a exportar desde Odoo y tildá la casilla \"Quiero actualizar datos "
    "(exportación compatible con importación)\". Sin esa columna Odoo crea productos "
    "nuevos en vez de actualizar los que ya existen, y eso no se puede deshacer.")

COLUMNAS = ["id_externo", "referencia", "default_code", "nombre_completo", "producto", "proveedor",
            "costo_actual", "precio_venta", "sitio_web", "categoria", "etiquetas",
            "stock", "publicado", "marcas", "clave", "clave_prov"]


def a_numero(v):
    """Un costo como texto: "1234.5", "1.234,50" o "1,234.50" (según la configuración
    regional de quien exportó). Lo que no se entiende queda como está (y cuenta como 0)."""
    t = str(v).strip().replace("$", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}", t) else t.replace(",", "")
    return t


def partir(nombre):
    """'Yerba 1 kg - Andresito - Andresito' -> ('Yerba 1 kg', 'Andresito')"""
    if " - " in nombre:
        prod, prov = nombre.rsplit(" - ", 1)
        return prod.strip(), prov.strip()
    return nombre.strip(), ""


def marcas_de(nombre):
    """Todos los segmentos después del producto: 'La Quesera' y 'Lácteos Don Julio'
    son ambos nombres válidos para el mismo proveedor."""
    partes = [p.strip() for p in nombre.split(" - ")[1:]]
    vistas, salida = set(), []
    for p in partes:
        k = normalizar(p)
        if k and k not in vistas:
            vistas.add(k)
            salida.append(p)
    return " | ".join(salida)


def leer_export(path):
    """Lee el export tal cual lo baja Odoo (CSV o Excel), todo como texto."""
    p = str(path).lower()
    try:
        if p.endswith((".csv", ".txt")):
            return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        return pd.read_excel(path, dtype=str)
    except Exception as e:  # archivo roto, otro formato, etc.
        raise ErrorDeDatos(f"No pude leer el export de Odoo ({e}). "
                           "Tiene que ser el archivo .csv o .xlsx que baja Odoo al exportar.")


def construir(fuente, quitar_repetidos=True, no_son_productos=()):
    """fuente: ruta al export o un DataFrame ya leído. Devuelve el catálogo.

    quitar_repetidos=False deja los productos con nombre repetido: la verificación
    después de importar los necesita, porque un duplicado es justo lo que busca.
    no_son_productos: comienzos de nombre propios del negocio que no son productos."""
    df = fuente.copy() if isinstance(fuente, pd.DataFrame) else leer_export(fuente)
    df = df.rename(columns=lambda c: str(c).strip())
    cols_norm = {c: sin_acentos(str(c)).lower().strip() for c in df.columns}

    def col(*cands):
        """Busca por nombre técnico exacto primero, después por fragmento en castellano."""
        for cand in cands:
            for c, n in cols_norm.items():
                if n == cand:
                    return c
        for cand in cands:
            for c, n in cols_norm.items():
                if cand in n:
                    return c
        return None

    c_id = next((c for c, n in cols_norm.items() if n in NOMBRES_ID), None)
    if c_id is None:
        raise ErrorDeDatos(SIN_ID)
    c_nom = col("name", "nombre")
    if c_nom is None:
        raise ErrorDeDatos("El export de Odoo no tiene la columna del nombre del producto "
                           "(\"name\"). Agregala al exportar.")
    c_cost = col("standard_price", "costo")
    # sin la columna de costo se puede seguir: todo queda como "no tenía costo cargado" y se
    # confirma a mano. El archivo para Odoo lleva el costo nuevo, no necesita el viejo.
    c_ref = col("default_code", "referencia interna")
    c_pv = col("list_price", "precio de venta")
    c_web = col("website_id", "sitio web")
    c_cat = col("categ_id", "categoria del producto")
    c_tag = col("product_tag_ids", "etiqueta")
    c_stk = col("qty_available", "cantidad a la mano")
    c_pub = col("is_published", "publicado")

    # Odoo exporta etiquetas múltiples en filas extra sin nombre: las colapsamos
    cont = df[c_nom].isna() | df[c_nom].astype(str).str.strip().eq("")
    df["_grp"] = (~cont).cumsum()
    if c_tag:
        tags = df.groupby("_grp")[c_tag].apply(
            lambda s: "; ".join(sorted({str(x).strip() for x in s.dropna()})))
    df = df[~cont].copy()
    if c_tag:
        df["etiquetas"] = tags.reindex(df["_grp"]).values

    texto = lambda c: df[c].fillna("").astype(str).str.strip() if c else ""
    numero = lambda c: pd.to_numeric(df[c].map(a_numero), errors="coerce").fillna(0.0) if c else 0.0

    df["nombre_completo"] = df[c_nom].astype(str).str.strip()
    df = df[~df["nombre_completo"].str.match(NO_PRODUCTO)]
    propios = [sin_acentos(t).lower().strip() for t in no_son_productos if str(t).strip()]
    if propios:
        empieza = df["nombre_completo"].map(lambda n: sin_acentos(n).lower().startswith(tuple(propios)))
        df = df[~empieza]
    # el tipo de producto de Odoo ("servicio" vs "almacenable") no se usa para filtrar acá:
    # para un negocio que vende servicios, son sus productos. Lo que un negocio puntual no
    # vende (una cuota social, un flete) se excluye por nombre en no_son_productos, arriba.

    partes = df["nombre_completo"].map(partir)
    df["producto"] = partes.map(lambda p: p[0])
    df["proveedor"] = partes.map(lambda p: p[1])
    df["id_externo"] = texto(c_id)
    # "referencia" es la clave con la que la app identifica cada producto (memoria, alias,
    # aprobaciones). Muchos negocios no cargan la Referencia interna en Odoo: sin ella, todos
    # los productos quedaban con la misma clave vacía y no se podía elegir ni aprobar ninguno.
    # El ID externo siempre está (sin él no se llega hasta acá). La Referencia interna real
    # queda en default_code: es lo único que se escribe de vuelta en Odoo.
    df["default_code"] = texto(c_ref)
    df["referencia"] = [dc or ide for dc, ide in zip(df["default_code"], df["id_externo"])]
    df["costo_actual"] = numero(c_cost)
    df["precio_venta"] = numero(c_pv)
    df["sitio_web"] = texto(c_web)
    df["categoria"] = texto(c_cat)
    df["stock"] = numero(c_stk)
    df["publicado"] = (df[c_pub].astype(str).str.strip().str.lower()
                       .isin(["true", "1", "verdadero", "si", "sí"]) if c_pub else True)
    if "etiquetas" not in df:
        df["etiquetas"] = ""
    df["marcas"] = df["nombre_completo"].map(marcas_de)
    df["clave"] = df["producto"].map(normalizar)
    df["clave_prov"] = df["proveedor"].map(normalizar)

    out = df[COLUMNAS]
    if quitar_repetidos:
        # variantes (talles, colores) comparten el nombre pero no la Referencia interna: son
        # productos distintos. Quitarlas por nombre mandaba el costo de un talle a otro.
        out = out.drop_duplicates(["nombre_completo", "default_code"])
    out = out.reset_index(drop=True)
    out["etiquetas"] = out["etiquetas"].fillna("")
    return out


def resumen(cat):
    return {"productos": len(cat),
            "proveedores": int(cat["proveedor"].replace("", pd.NA).nunique()),
            "sin_referencia": int((cat["default_code"] == "").sum()),
            "sin_id": int((cat["id_externo"] == "").sum())}
