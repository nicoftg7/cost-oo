"""Clasifica el análisis (listos / confirmar / avisos) y escribe los archivos.

Redes de seguridad:
  - Al import solo entra lo que matchea con score >= 88 y varía <= 30%.
  - Un mismo producto no puede entrar dos veces (Odoo aplicaría el último sin avisar).
  - Un cambio de presentación frena el costo: solo se corrige lo aprobado.
  - Nada se despublica solo: los faltantes se avisan.
  - Ninguna columna de inventario: las cantidades nunca se tocan.
  - Antes de escribir un archivo de importación se verifica que todos los id sean
    ID externos de Odoo. Si no, el archivo NO se genera.
"""
import hashlib
import re

import pandas as pd

from . import ErrorDeDatos
from .normalize import TAMANO, expandir, normalizar

UMBRAL_VARIACION = 30

ID_VALIDO = re.compile(r"^[A-Za-z_][\w.]*\.[\w.-]+$")   # __export__.product_template_9_56c50ae4

COLS_REV = ["proveedor", "referencia", "nombre_odoo", "descripcion", "costo_actual", "costo_nuevo",
            "variacion_%", "precio_sugerido", "pv_actual", "score", "via", "fuente", "origen", "codigo", "linea"]

UMBRAL_REVISAR_A_OJO = 15   # en el control final se marca lo que cambia más que esto


def clave_renglon(r):
    """Identifica un renglón del mensaje de forma estable entre corridas."""
    base = f"{r.get('proveedor_bloque', '')}|{r.get('linea', '')}|{r.get('descripcion', '')}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def verificar_ids(df, nombre_archivo):
    """Último control antes de escribir un archivo de importación.

    Si la columna id no son IDs externos de Odoo, la importación CREA productos
    nuevos en vez de actualizar, y eso no se deshace desde un archivo. Preferimos
    no generar el archivo antes que generar uno que duplique el catálogo.
    """
    if "id" not in df.columns:
        raise ErrorDeDatos(f"No se generó {nombre_archivo}: le falta la columna id.")
    ids = df["id"].astype(str).str.strip()
    malos = ids[~ids.str.match(ID_VALIDO)]
    if len(malos):
        raise ErrorDeDatos(
            f"No se generó {nombre_archivo}: {len(malos)} productos no tienen un ID externo "
            f"de Odoo válido (ejemplos: {', '.join(malos.head(5).tolist())}).\n"
            "Importarlo crearía productos duplicados. Revisá que el export de Odoo se haya "
            "hecho con la casilla \"Quiero actualizar datos\" tildada.")
    if ids.duplicated().any():
        raise ErrorDeDatos(f"No se generó {nombre_archivo}: hay productos repetidos.")
    if "standard_price" in df:
        precios = pd.to_numeric(df["standard_price"], errors="coerce")
        if precios.isna().any() or (precios <= 0).any():
            raise ErrorDeDatos(f"No se generó {nombre_archivo}: hay costos vacíos o en cero.")


def corregir_tamano(texto_proveedor, nombre_odoo):
    """Reescribe el gramaje del nombre de Odoo con el que cotiza el proveedor.
    'Chimichurri x 50 g' + 'Chimichurri x 25 g' -> 'Chimichurri x 25 g'"""
    nuevos = TAMANO.findall(normalizar(expandir(texto_proveedor)))
    if not nuevos:
        return nombre_odoo
    valor, unidad = nuevos[0]
    equiv = {"g": r"(gramos?|grs?|gr|g)", "cc": r"(cc|ml|mililitros?)",
             "u": r"(unidades?|unid\.?|un|u|uds?)"}
    patron = rf"\b\d+(?:[.,]\d+)?\s*{equiv[unidad]}\b"
    corregido, n = re.subn(patron, f"{valor} {unidad}", nombre_odoo, count=1, flags=re.I)
    return corregido if n else nombre_odoo


def rotacion(d, cat, grupos):
    """Grupos de variantes que el proveedor alterna: la que mandó va publicada, la otra no.
    Solo se evalúa si el proveedor mandó algo del grupo en este ciclo."""
    if not len(grupos):
        return pd.DataFrame()
    mencionadas = set(d.get("referencia", pd.Series(dtype=str)).dropna())
    filas = []
    for grupo, miembros in grupos.groupby("grupo"):
        if not (set(miembros.referencia) & mencionadas):
            continue          # el proveedor no mandó nada de este grupo: no se toca
        for m in miembros.itertuples():
            c = cat[cat.referencia == m.referencia]
            if not len(c):
                continue
            c = c.iloc[0]
            deberia = m.referencia in mencionadas
            filas.append({"grupo": grupo, "referencia": m.referencia, "default_code": c.default_code,
                          "nombre_completo": c.nombre_completo, "id_externo": c.id_externo,
                          "mencionado": deberia, "publicado_hoy": bool(c.publicado),
                          "deberia_estar": deberia,
                          "accion": "sin cambio" if bool(c.publicado) == deberia
                                    else ("PUBLICAR" if deberia else "DESPUBLICAR")})
    return pd.DataFrame(filas)


def _vacio(cols):
    return pd.DataFrame(columns=cols)


def clasificar(d, notas, cat, memoria, aprobados_ciclo=(), saltados=(), fuente_ok=(),
               umbral_variacion=UMBRAL_VARIACION):
    """Separa el análisis en lo que entra solo, lo que hay que confirmar y los avisos.

    aprobados_ciclo: {(clave, referencia)} aprobados a mano solo para este ciclo
                     (variación fuerte, producto que no tenía costo).
    saltados:        {clave} renglones que la persona decidió dejar afuera este ciclo.
    fuente_ok:       {(clave, referencia)} "sí, ahora se lo compro a este proveedor".
    """
    from .providers import mismo_proveedor
    aprobados_ciclo, saltados, fuente_ok = set(aprobados_ciclo), set(saltados), set(fuente_ok)
    habituales = memoria.fuentes_habituales()
    aprob = memoria.leer("aprobados")
    ok_gramaje = set(aprob[aprob.tipo == "gramaje"].referencia)            # renombrar en Odoo
    ok_presentacion = set(aprob[aprob.tipo == "presentacion"].referencia)  # dejar el nombre como está
    ok_variacion = set(aprob[aprob.tipo == "variacion"].referencia)

    if len(d):
        d = d.copy()
        for c in ("referencia", "default_code", "desajuste", "via", "nombre_odoo", "alternativas",
                  "mes", "fuente", "origen", "codigo", "huella", "proveedor", "proveedor_bloque"):
            if c not in d:
                d[c] = ""
            d[c] = d[c].fillna("")
        for c in ("costo_actual", "variacion_%", "costo_nuevo", "score"):
            if c not in d:
                d[c] = float("nan")
        if "falta_iva" not in d:
            d["falta_iva"] = False
        d["falta_iva"] = d["falta_iva"].fillna(False).astype(bool)
        if "opciones" not in d:
            d["opciones"] = [[] for _ in range(len(d))]
        d["opciones"] = d["opciones"].map(lambda o: o if isinstance(o, list) else [])
        d["clave_renglon"] = [clave_renglon(r) for r in d.to_dict("records")]
        d["fuente_habitual"] = d["referencia"].map(lambda r: habituales.get(r, "") if r else "")

    # lo que la lista trae y no se vende: no se muestra uno por uno
    fuera = d[d.estado.eq("fuera de catálogo")] if len(d) else _vacio([])
    if len(d):
        d = d[~d.estado.eq("fuera de catálogo")]

    costos = d[d.tipo.eq("costo") & ~d.estado.eq("ignorado")].copy() if len(d) else _vacio(COLS_REV)
    saltados_df = costos[costos.clave_renglon.isin(saltados)] if len(costos) else _vacio(COLS_REV)
    if len(costos):
        costos = costos[~costos.clave_renglon.isin(saltados)]
    if len(costos):
        costos["alerta"] = ""
        costos.loc[costos["estado"].eq("revisar"), "alerta"] = "match a confirmar"
        costos.loc[costos["score"] < 75, "alerta"] = "match dudoso"
        costos.loc[costos["costo_actual"].fillna(0).eq(0), "alerta"] = "no tenía costo cargado"
        costos.loc[costos["variacion_%"].abs() > umbral_variacion, "alerta"] = "variación fuerte"
        # un cambio de presentación sin aprobar frena el costo: puede no ser comparable
        costos.loc[costos.desajuste.astype(str).str.strip().ne("") &
                   ~costos.referencia.isin(ok_gramaje | ok_presentacion),
                   "alerta"] = "presentación distinta"
        costos.loc[costos.referencia.isin(ok_variacion), "alerta"] = ""
        # aprobado a mano en este ciclo: solo levanta variación fuerte / sin costo previo
        aprob_mask = pd.Series([(k, r) in aprobados_ciclo for k, r in
                                zip(costos.clave_renglon, costos.referencia)], index=costos.index)
        costos.loc[aprob_mask & costos.alerta.isin(["variación fuerte", "no tenía costo cargado"]),
                   "alerta"] = ""
        # el producto se le compra a otro: una lista grande o un mensaje de otro proveedor
        # no pisa el costo sin preguntar (el atún Bahía lo venden varias distribuidoras)
        otra = pd.Series([bool(h) and bool(f) and not mismo_proveedor(h, f) and (k, r) not in fuente_ok
                          for h, f, k, r in zip(costos.fuente_habitual, costos.fuente,
                                                costos.clave_renglon, costos.referencia)],
                         index=costos.index)
        costos.loc[otra & ~costos.estado.eq("sin cambio"), "alerta"] = "otra fuente"
        # sin el IVA del producto no hay costo: nada lo levanta salvo decir qué IVA lleva.
        # Se pregunta recién cuando se sabe qué producto es (primero el producto, después el IVA).
        costos.loc[costos["falta_iva"] & costos.estado.eq("ok"), "alerta"] = "falta IVA"
        costos.loc[costos["estado"].eq("sin match"), "alerta"] = "sin match"
        listos = costos[costos.alerta.eq("") & costos.estado.eq("ok") & ~costos["falta_iva"]]
        revisar = costos[~costos.index.isin(listos.index) & ~costos.estado.eq("sin cambio")]
        sin_cambio = costos[costos.estado.eq("sin cambio")]
    else:
        listos = revisar = sin_cambio = costos.assign(alerta="")

    # Red de seguridad: un mismo producto no puede entrar dos veces al import.
    # Si dos renglones apuntan al mismo producto (un proveedor cotiza "BONDIOLA" y
    # "BONDIOLA FET"), gana el de más confianza y el otro va a confirmar.
    # A igual confianza gana la fuente cargada más tarde (un mensaje suelto suele
    # corregir a la planilla).
    if len(listos):
        listos = listos.assign(_orden=listos.get("orden_fuente", pd.Series(0, index=listos.index)).fillna(0))
        orden = listos.sort_values(["score", "_orden"], ascending=False, kind="stable")
        ganadores = orden.drop_duplicates("referencia", keep="first")
        perdedores = orden[~orden.index.isin(ganadores.index)]
        if len(perdedores):
            g = ganadores.set_index("referencia")
            perdedores = perdedores.assign(
                alerta="otro renglón matcheó el mismo producto con más confianza",
                gana=perdedores.referencia.map(g["clave_renglon"]),
                gana_linea=perdedores.referencia.map(g["linea"]),
                gana_costo=perdedores.referencia.map(g["costo_nuevo"]),
                gana_origen=perdedores.referencia.map(g["origen"]))
            revisar = pd.concat([revisar, perdedores.drop(columns="_orden")])
        listos = listos[listos.index.isin(ganadores.index)].drop(columns="_orden")

    # El proveedor cotiza otra presentación. NO se corrige solo: "25 g contra 50 g"
    # puede ser un envase nuevo, pero "200 g contra 360 g" es otro producto.
    if len(costos):
        desaj = costos[costos.desajuste.astype(str).str.strip().ne("")].copy()
    else:
        desaj = _vacio(COLS_REV)
    if len(desaj):
        desaj["aprobado"] = desaj.referencia.isin(ok_gramaje)
        desaj["nombre_corregido"] = [corregir_tamano(r.descripcion, r.nombre_odoo)
                                     if r.aprobado else "" for r in desaj.itertuples()]
        desaj_ok = desaj[desaj.aprobado]
    else:
        desaj_ok = desaj
    nombres = dict(zip(desaj_ok.referencia, desaj_ok.nombre_corregido)) if len(desaj_ok) else {}

    avisos = d[d.tipo.isin(["faltante", "sin cambio"])] if len(d) else _vacio([])
    ignorados = d[d.estado.eq("ignorado")] if len(d) else _vacio([])
    rot = rotacion(d if len(d) else pd.DataFrame(), cat, memoria.leer("rotaciones"))

    # productos del proveedor que no aparecieron en ningún mensaje de este ciclo
    provs = [p for p in d.get("proveedor", pd.Series(dtype=str)).dropna().unique() if p] if len(d) else []
    mencionadas = set(d.get("referencia", pd.Series(dtype=str)).dropna()) if len(d) else set()
    en_rotacion = set(rot.referencia) if len(rot) else set()
    no_mencionados = cat[cat.proveedor.isin(provs) & ~cat.referencia.isin(mencionadas)
                         & ~cat.referencia.isin(en_rotacion)][
        ["proveedor", "referencia", "nombre_completo", "costo_actual", "publicado"]]

    return {"analisis": d, "listos": listos, "confirmar": revisar, "sin_cambio": sin_cambio,
            "saltados": saltados_df, "avisos": avisos, "notas": notas, "ignorados": ignorados,
            "gramajes": desaj, "nombres_corregidos": nombres, "rotacion": rot,
            "no_mencionados": no_mencionados, "fuera_de_catalogo": fuera}


def control(res, margen_variable=(), umbral=UMBRAL_REVISAR_A_OJO):
    """La última mirada antes (y después) de importar: todo lo que va a cambiar en Odoo,
    con lo raro arriba. Cada fila dice de qué mensaje o lista salió el costo.

    Margen fijo (lo normal): el precio de venta va a subir lo mismo que el costo, así que
    se muestra el precio estimado. margen_variable: proveedores cuyo precio no sigue al
    costo (por ejemplo, bebidas que se precian aparte): ahí el costo nuevo se compara contra el precio de venta de hoy."""
    from .providers import mismo_proveedor
    listos = res["listos"]
    if not len(listos):
        return pd.DataFrame()
    nombres = res["nombres_corregidos"]
    filas = []
    for r in listos.to_dict("records"):
        antes, nuevo = r.get("costo_actual") or 0, float(r["costo_nuevo"])
        pv = r.get("pv_actual") or 0
        var = r.get("variacion_%")
        variable = any(mismo_proveedor(p, v) for v in margen_variable
                       for p in (r.get("fuente", ""), r.get("proveedor", "")) if p)
        marcas = []
        if variable:
            pv_estimado = pv
            margen = round((pv - nuevo) / pv * 100, 1) if pv else None
            if pv and nuevo >= pv:
                marcas.append("el costo nuevo es mayor o igual al precio de venta")
            elif pv and (pv - nuevo) / pv < 0.10:
                marcas.append("queda menos de 10% de margen")
        else:
            pv_estimado = round(pv * nuevo / antes, 2) if pv and antes else None
            margen = round((pv - antes) / pv * 100, 1) if pv and antes else None
        if var == var and var is not None and abs(var) > umbral:
            marcas.append(f"cambia {var:+.0f}%")
        if var == var and var is not None and var < 0:
            marcas.append("baja de precio")
        if not antes:
            marcas.append("no tenía costo")
        if r.get("referencia") in nombres:
            marcas.append(f"cambia el nombre a: {nombres[r['referencia']]}")
        filas.append({"referencia": r.get("default_code", ""), "producto": r.get("nombre_odoo", ""),
                      "costo_antes": antes, "costo_nuevo": nuevo, "variacion_%": var,
                      "precio_venta": pv or None, "precio_venta_estimado": pv_estimado,
                      "margen_%": margen, "margen": "variable" if variable else "fijo",
                      "fuente": r.get("fuente", ""), "origen": r.get("origen", ""),
                      "codigo": r.get("codigo", ""), "dice": r.get("linea", ""),
                      "revisar": " · ".join(marcas)})
    df = pd.DataFrame(filas)
    df["_orden"] = df["revisar"].ne("")
    df["_abs"] = df["variacion_%"].abs().fillna(0)
    return df.sort_values(["_orden", "_abs"], ascending=False).drop(columns=["_orden", "_abs"]).reset_index(drop=True)


def filas_historial(res, fecha):
    """Lo que se manda a Odoo queda registrado con su fuente. Los 'sin cambio' también
    sirven: confirman a quién se le compra cada producto."""
    filas = []
    for r in res["listos"].to_dict("records"):
        filas.append({"fecha": fecha, "referencia": r["referencia"], "producto": r.get("nombre_odoo", ""),
                      "costo_anterior": r.get("costo_actual", ""), "costo_nuevo": r["costo_nuevo"],
                      "fuente": r.get("fuente", ""), "origen": r.get("origen", ""),
                      "linea": r.get("linea", ""), "estado": "generado"})
    for r in res["sin_cambio"].to_dict("records"):
        if r.get("fuente") and not r.get("fuente_habitual"):
            filas.append({"fecha": fecha, "referencia": r["referencia"], "producto": r.get("nombre_odoo", ""),
                          "costo_anterior": r.get("costo_actual", ""), "costo_nuevo": r["costo_nuevo"],
                          "fuente": r["fuente"], "origen": r.get("origen", ""),
                          "linea": r.get("linea", ""), "estado": "sin cambio"})
    return filas


def tabla_import(res):
    listos = res["listos"]
    nombres = res["nombres_corregidos"]
    # default_code es la Referencia interna real (puede estar vacía); "referencia" es la clave
    # interna de la app y, sin Referencia interna, es el ID externo: nunca va a Odoo
    return pd.DataFrame({"id": listos.id_externo.values,
                         "default_code": listos.default_code.values,
                         "name": [nombres.get(r, n) for r, n in zip(listos.referencia, listos.nombre_odoo)],
                         "standard_price": listos.costo_nuevo.astype(float).round(2).values})


def escribir_import(res, ruta):
    """El archivo que se importa en Odoo. Devuelve la ruta, o None si no hay nada listo."""
    if not len(res["listos"]):
        return None
    imp = tabla_import(res)
    verificar_ids(imp, "el archivo de importación")
    imp.to_excel(ruta, index=False)
    return ruta


def escribir_rotacion(res, ruta):
    rot = res["rotacion"]
    if not len(rot) or not (rot.accion != "sin cambio").any():
        return None
    cambios = rot[rot.accion != "sin cambio"]
    tabla = pd.DataFrame({"id": cambios.id_externo.values, "default_code": cambios.default_code.values,
                          "name": cambios.nombre_completo.values,
                          "is_published": cambios.deberia_estar.map({True: "TRUE", False: "FALSE"}).values})
    verificar_ids(tabla, "el archivo de publicación")
    tabla.to_excel(ruta, index=False)
    return ruta


def escribir_reporte(res, ruta, margen_variable=()):
    with pd.ExcelWriter(ruta) as w:
        def hoja(df, nombre, cols):
            (df.reindex(columns=cols) if len(df) else pd.DataFrame(columns=cols)
             ).to_excel(w, sheet_name=nombre, index=False)
        d = res["analisis"]
        ctl = control(res, margen_variable)
        hoja(ctl, "Control", list(ctl.columns) if len(ctl) else ["producto"])
        hoja(res["listos"], "Listos", COLS_REV)
        hoja(res["confirmar"], "Confirmar", COLS_REV + ["alerta", "alternativas"])
        hoja(res["gramajes"], "Gramajes", ["proveedor", "referencia", "descripcion", "nombre_odoo",
                                           "nombre_corregido", "aprobado", "desajuste",
                                           "costo_actual", "costo_nuevo", "linea"])
        hoja(res["ignorados"], "Ignorados", ["proveedor", "descripcion", "costo_nuevo", "via", "linea"])
        hoja(d[d.estado.eq("sin match")] if len(d) else d, "No están en Odoo",
             ["proveedor", "descripcion", "costo_nuevo", "alternativas", "linea"])
        hoja(res["saltados"], "Salteados", ["proveedor", "descripcion", "costo_nuevo", "linea"])
        hoja(res["sin_cambio"], "Sin cambio", COLS_REV)
        hoja(res["avisos"], "Avisos", ["proveedor", "tipo", "referencia", "nombre_odoo", "publicado",
                                       "mes", "costo_actual", "score", "linea"])
        hoja(res["notas"], "Notas", ["proveedor", "nota", "linea"])
        rot = res["rotacion"]
        hoja(rot, "Rotación", list(rot.columns) if len(rot) else
             ["grupo", "referencia", "nombre_completo", "mencionado", "publicado_hoy",
              "deberia_estar", "accion"])
        hoja(res["no_mencionados"], "No mencionados", list(res["no_mencionados"].columns))
        hoja(res["fuera_de_catalogo"], "Listas - no se venden",
             ["fuente", "codigo", "descripcion", "costo_nuevo", "linea"])
    return ruta
