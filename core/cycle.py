"""Un ciclo completo: export de Odoo + una o varias fuentes de precios -> resultado.

Tipos de fuente (cada una es un dict):
  planilla  la planilla de seguimiento (Proveedor / Chequeado / Mensaje), bajada como
            CSV o leída directo de Google Sheets. Cada fila se analiza por separado y
            sabe de qué proveedor es.
  mensaje   un mensaje suelto, con el proveedor elegido a mano.
  texto     texto pegado o .txt con varios proveedores (el nombre arriba de cada bloque).
  lista     lista de precios de una distribuidora (PDF, Excel o CSV) con código propio.

Cada renglón lleva "fuente" (a quién se le compra) y "origen" (de qué mensaje o lista
salió), para poder rastrear cualquier costo después.
"""
import datetime as dt
from pathlib import Path

import pandas as pd

from . import ErrorDeDatos
from .catalog import construir
from .match import Contexto, analizar, analizar_lista
from .output import clasificar
from .parse import es_planilla, extraer_renglones, huella, leer_bloques, leer_filas_planilla
from .pricelist import Lectura, leer_fuente
from .providers import es_proveedor

COLS_NOTAS = ["proveedor", "nota", "linea", "huella", "origen"]

# a partir de acá se avisa que la fecha de la lista es vieja, por si mandaron una desactualizada
DIAS_LISTA_VIEJA = 60

# leer un PDF grande tarda unos segundos: se lee una vez por ciclo, no en cada decisión
_CACHE_LISTAS = {}


def _renglones_de_texto(texto, ctx, fuente, origen, huella_, inicial=""):
    bloques = leer_bloques(texto, ctx.idx, inicial=inicial)
    renglones, notas = extraer_renglones(bloques)
    for r in renglones:
        # sin fuente fija (texto con varios proveedores): se le compra a quien encabeza el bloque
        r.update({"fuente": fuente or r.get("proveedor_bloque", ""), "origen": origen, "huella": huella_})
    for n in notas:
        n.update({"origen": origen, "huella": huella_})
    return renglones, notas


def _ya_procesado(memoria, h, forzados, proveedor, texto, origen, notas, info):
    """Si este mismo texto ya se procesó en un ciclo anterior, se avisa y no se analiza."""
    cuando = memoria.procesado(h)
    if not cuando or h in forzados:
        return False
    info["ya_procesados"] += 1
    notas.append({"proveedor": proveedor, "nota": f"mensaje ya procesado el {cuando}: son los mismos precios",
                  "linea": texto[:120], "huella": h, "origen": origen})
    return True


def aviso_de_fecha_vieja(fecha):
    """None si la fecha es reciente; si no, el texto para avisar que puede estar desactualizada."""
    if not fecha:
        return None
    dias = (dt.date.today() - fecha).days
    if dias <= DIAS_LISTA_VIEJA:
        return None
    meses = dias // 30
    return f"la lista dice {fecha.isoformat()}, tiene {meses} {'mes' if meses == 1 else 'meses'}: ¿es la última?"


def avisos_de_la_planilla(fuentes):
    """Pestañas Lista y Manual de la planilla: qué lista falta cargar en este ciclo y qué
    proveedores se actualizan a mano (la app no los toca, solo los recuerda)."""
    from .providers import mismo_proveedor
    notas = []
    cargadas = [f["proveedor"] for f in fuentes if f["tipo"] == "lista"]
    for f in fuentes:
        for prov in f.get("listas_esperadas", []):
            if not any(mismo_proveedor(prov, c, corte=90) for c in cargadas):
                notas.append({"proveedor": prov, "nota": "manda lista: todavía no se cargó en este ciclo",
                              "linea": "", "origen": f.get("nombre", "")})
        for prov, chequeado in f.get("manual", []):
            notas.append({"proveedor": prov,
                          "nota": "se actualiza a mano" + (" (ya chequeado)" if chequeado else ": falta chequear"),
                          "linea": "", "origen": f.get("nombre", "")})
    return notas


def correr_fuentes(catalogo, memoria, fuentes, aprobados_ciclo=(), saltados=(), fuente_ok=(),
                   forzados=(), factor=1.0):
    """factor: en un ciclo de práctica, los precios se multiplican (1.10 = simular un 10% de
    aumento) para que listas del mes pasado parezcan un ciclo nuevo."""
    ctx = Contexto(catalogo, memoria)
    forzados = set(forzados)
    renglones, notas, filas_lista, huellas, resumen = [], [], [], [], []

    for orden, f in enumerate(fuentes):
        tipo, nombre = f["tipo"], f.get("nombre", "")
        n_renglones, n_lista = len(renglones), len(filas_lista)
        info = {"nombre": nombre, "tipo": tipo, "analizados": 0, "ya_procesados": 0,
                "pendientes": 0, "sin_cambios": 0, "articulos": 0, "no_leidos": []}

        if tipo == "planilla":
            for fila in leer_filas_planilla(f["contenido"]):
                prov = fila["proveedor"]
                if fila["chequeado"] is False:
                    info["pendientes"] += 1
                    notas.append({"proveedor": prov, "nota": "pendiente: todavía no mandó precios",
                                  "linea": "", "origen": nombre})
                    continue
                if not fila["mensaje"]:
                    info["sin_cambios"] += 1
                    nota = ("chequeado sin mensaje: mantiene los mismos precios" if fila["chequeado"]
                            else "sin mensaje en la planilla")
                    notas.append({"proveedor": prov, "nota": nota, "linea": "", "origen": nombre})
                    continue
                h = huella(prov, fila["mensaje"])
                cuando = memoria.procesado(h)
                if cuando and h not in forzados:
                    info["ya_procesados"] += 1
                    nota = f"mensaje ya procesado el {cuando}: son los mismos precios"
                    if fila["actualizado"]:
                        nota += f" (la planilla dice actualizado: {fila['actualizado']})"
                    notas.append({"proveedor": prov, "nota": nota, "linea": fila["mensaje"][:120],
                                  "huella": h, "origen": nombre})
                    continue
                info["analizados"] += 1
                huellas.append({"fuente": prov, "huella": h, "origen": nombre})
                inicial = es_proveedor(prov, ctx.idx) or prov
                r, n = _renglones_de_texto(fila["mensaje"], ctx, prov, nombre, h, inicial)
                renglones += r
                notas += n

        elif tipo == "mensaje":
            prov = f["proveedor"]
            h = huella(prov, f["texto"])
            if _ya_procesado(memoria, h, forzados, prov, f["texto"], nombre, notas, info):
                resumen.append(info)
                continue
            info["analizados"] = 1
            huellas.append({"fuente": prov, "huella": h, "origen": nombre})
            inicial = es_proveedor(prov, ctx.idx) or prov
            r, n = _renglones_de_texto(f["texto"], ctx, prov, nombre, h, inicial)
            renglones += r
            notas += n

        elif tipo == "texto":
            texto = f.get("texto") or f["contenido"].decode("utf-8-sig", errors="replace")
            h = huella(texto)
            if _ya_procesado(memoria, h, forzados, nombre, texto, nombre, notas, info):
                resumen.append(info)
                continue
            info["analizados"] = 1
            r, n = _renglones_de_texto(texto, ctx, "", nombre, h)
            renglones += r
            notas += n
            for prov in {x["fuente"] for x in r if x["fuente"]}:
                huellas.append({"fuente": prov, "huella": h, "origen": nombre})

        elif tipo == "lista":
            distribuidor = f["proveedor"]
            h = f.get("huella") or huella(distribuidor, f["contenido"].hex()[:20000])
            cuando = memoria.procesado(h)
            if cuando and h not in forzados:
                info["ya_procesados"] = 1
                notas.append({"proveedor": distribuidor, "nota": f"esta misma lista ya se procesó el {cuando}",
                              "linea": nombre, "huella": h, "origen": nombre})
                resumen.append(info)
                continue
            if f.get("contenido") is not None:
                # se lee con el catálogo a mano: así se reconocen las marcas de la lista.
                # Leer un PDF grande o una foto tarda: se lee una vez por ciclo.
                clave = (h, id(catalogo), len(catalogo))
                if clave not in _CACHE_LISTAS:
                    _CACHE_LISTAS[clave] = leer_fuente(nombre, f["contenido"], ctx.idx)
                lectura = _CACHE_LISTAS[clave]
            else:
                lectura = Lectura(articulos=f["articulos"], no_leidos=f.get("no_leidos", []), fecha=f.get("fecha"))
            info["no_leidos"] = lectura.no_leidos
            info["fecha"] = lectura.fecha.isoformat() if lectura.fecha else ""
            info["de_foto"] = lectura.de_foto
            aviso = aviso_de_fecha_vieja(lectura.fecha)
            if aviso:
                info["aviso_fecha"] = aviso
            huellas.append({"fuente": distribuidor, "huella": h, "origen": nombre})
            if not lectura.articulos:
                # sin códigos (un mensaje en PDF, una foto): se lee como mensaje de ese proveedor
                info["como_mensaje"] = True
                info["analizados"] = 1
                inicial = es_proveedor(distribuidor, ctx.idx) or distribuidor
                r, n = _renglones_de_texto(lectura.texto, ctx, distribuidor, nombre, h, inicial)
                renglones += r
                notas += n
            else:
                articulos = [dict(a) for a in lectura.articulos]
                info["articulos"] = len(articulos)
                for a in articulos:
                    # el precio anterior no se simula: representa lo que ya está en Odoo
                    a.update({"fuente": distribuidor, "origen": nombre, "huella": h,
                              "precio": round(a["precio"] * factor, 2)})
                filas_lista += analizar_lista(articulos, ctx, distribuidor)
        else:
            raise ErrorDeDatos(f"Tipo de fuente desconocido: {tipo}")
        for r in renglones[n_renglones:]:
            r["orden_fuente"] = orden
        for r in filas_lista[n_lista:]:
            r["orden_fuente"] = orden
        resumen.append(info)

    notas += avisos_de_la_planilla(fuentes)
    if factor != 1.0:
        for r in renglones:
            if r.get("costo_nuevo") is not None:
                r["costo_nuevo"] = round(r["costo_nuevo"] * factor, 2)

    d = analizar(renglones, ctx)
    if filas_lista:
        d = pd.concat([d, pd.DataFrame(filas_lista)], ignore_index=True)
    res = clasificar(d, pd.DataFrame(notas, columns=COLS_NOTAS).fillna(""), catalogo, memoria,
                     aprobados_ciclo, saltados, fuente_ok)
    res["huellas"] = huellas
    res["fuentes"] = resumen
    return res


# ---------- atajos ----------
def fuente_de_archivo(nombre, contenido, proveedor=""):
    """Decide qué tipo de fuente es un archivo subido."""
    n = nombre.lower()
    if n.endswith((".pdf", ".xlsx", ".xls")):
        if not proveedor:
            raise ErrorDeDatos(f"Para la lista {nombre} falta decir de qué distribuidora es.")
        return {"tipo": "lista", "nombre": nombre, "proveedor": proveedor, "contenido": contenido}
    if es_planilla(nombre, contenido):
        return {"tipo": "planilla", "nombre": nombre, "contenido": contenido}
    if proveedor and n.endswith(".csv"):
        return {"tipo": "lista", "nombre": nombre, "proveedor": proveedor, "contenido": contenido}
    return {"tipo": "texto", "nombre": nombre, "contenido": contenido}


def correr(catalogo, memoria, texto, sin_cambios=(), pendientes=(), **kw):
    """Compatibilidad: un texto con uno o varios proveedores."""
    notas = [{"proveedor": p, "nota": "chequeado sin mensaje: mantiene los mismos precios"} for p in sin_cambios]
    notas += [{"proveedor": p, "nota": "pendiente: todavía no mandó precios"} for p in pendientes]
    res = correr_fuentes(catalogo, memoria, [{"tipo": "texto", "nombre": "texto", "texto": texto}], **kw)
    if notas:
        res["notas"] = pd.concat([pd.DataFrame(notas, columns=COLS_NOTAS).fillna(""), res["notas"]],
                                 ignore_index=True)
    return res


def correr_desde_archivos(path_export, paths, memoria, proveedores=None):
    """Para usar desde código o tests: rutas en disco. proveedores: {ruta: distribuidora}
    para las listas."""
    proveedores = proveedores or {}
    cat = construir(path_export, no_son_productos=memoria.no_son_productos())
    fuentes = [fuente_de_archivo(Path(p).name, Path(p).read_bytes(), proveedores.get(str(p), ""))
               for p in paths]
    return correr_fuentes(cat, memoria, fuentes)
