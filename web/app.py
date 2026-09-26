"""Interfaz web local: se abre en el navegador, se cargan las fuentes de precios y los
matcheos dudosos se confirman en pantalla. Cada confirmación se guarda en la memoria,
que es lo que hace que el mes siguiente salga solo.

Uso: python -m web.app   (o doble clic en "Abrir actualizador.bat")
"""
import datetime as dt
import io
import json
import os
import threading
import uuid
import webbrowser
from pathlib import Path

import pandas as pd
from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for

from core import ErrorDeDatos
from core.catalog import construir, resumen
from core.cycle import correr_fuentes, fuente_de_archivo
from core.memory import Memoria
from core.output import (control, escribir_import, escribir_reporte, escribir_rotacion,
                         filas_historial, tabla_import)
from core.parse import huella
from core.pricelist import leer_lista
from core.normalize import normalizar
from core.providers import indice_proveedores
from core import sheets
from core.verify import verificar
from core.transform import costo_de_lista
from core.pais import IVA_OPCIONES

RAIZ = Path(__file__).resolve().parents[1]
# ACTUALIZADOR_DATOS permite apuntar a otra carpeta (pruebas, o varios negocios)
DATOS = Path(os.environ.get("ACTUALIZADOR_DATOS") or RAIZ / "datos")
CICLO = DATOS / "ciclo_actual"
SALIDAS = DATOS / "salidas"
ESTADO = CICLO / "estado.json"
CONFIG = DATOS / "config.json"
PUERTO = int(os.environ.get("ACTUALIZADOR_PUERTO") or 8765)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
memoria = Memoria(DATOS / "memoria")

_cache = {"resultado": None, "catalogo": None}


def ahora():
    return dt.datetime.now().strftime("%d/%m/%Y %H:%M")


# ---------- configuración (link de la planilla) ----------
def leer_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return {}


def guardar_config(c):
    DATOS.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- estado del ciclo en curso (sobrevive a cerrar la app) ----------
def estado_vacio():
    return {"export": "", "export_original": "", "fuentes": [], "aprobados": [], "saltados": [],
            "fuente_ok": [], "forzados": [], "creado": dt.datetime.now().isoformat(timespec="minutes"),
            # práctica: lo que se confirma se aprende, pero no se registra historial ni se importa
            "practica": False, "subir": 0}


def en_practica(e):
    return bool(e and e.get("practica"))


@app.context_processor
def datos_de_todas_las_pantallas():
    """Todas las pantallas saben si el ciclo es de práctica (para el cartel) y el nombre del negocio."""
    e = leer_estado()
    return {"practica": en_practica(e), "practica_subir": (e or {}).get("subir", 0),
            "negocio": leer_config().get("negocio", ""), "modo_ejemplo": MODO_EJEMPLO}


def leer_estado():
    if ESTADO.exists():
        e = json.loads(ESTADO.read_text(encoding="utf-8"))
        for k, v in estado_vacio().items():
            e.setdefault(k, v)
        return e
    return None


def guardar_estado(e):
    CICLO.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
    _cache["resultado"] = None


def estado_o_nuevo():
    return leer_estado() or estado_vacio()


def catalogo(e):
    if _cache["catalogo"] is None and e and e.get("export"):
        _cache["catalogo"] = construir(CICLO / e["export"], no_son_productos=memoria.no_son_productos())
    return _cache["catalogo"]


def producto_por_nombre(cat, nombre):
    """La referencia del producto con ese nombre, o "" si no está. El navegador no siempre
    completa el valor exacto de la lista de sugerencias (mayúsculas, tildes, espacios de más
    si se tipeó a mano): sin coincidencia exacta, se compara ignorando eso."""
    if cat is None or not nombre:
        return ""
    hit = cat[cat.nombre_completo == nombre]
    if not len(hit):
        hit = cat[cat.nombre_completo.map(normalizar) == normalizar(nombre)]
    return hit.iloc[0]["referencia"] if len(hit) else ""


def cargar_fuentes(e):
    """Las fuentes del estado, con su contenido leído de disco."""
    salida = []
    for f in e["fuentes"]:
        f = dict(f)
        if f.get("archivo"):
            f["contenido"] = (CICLO / f["archivo"]).read_bytes()
        salida.append(f)
    return salida


def resultado():
    """Corre el ciclo con lo guardado en disco. Se cachea hasta la próxima decisión."""
    e = leer_estado()
    if not e or not e.get("export") or not e["fuentes"]:
        return e, None
    if _cache["resultado"] is None:
        _cache["resultado"] = correr_fuentes(
            catalogo(e), memoria, cargar_fuentes(e),
            aprobados_ciclo={tuple(x) for x in e["aprobados"]}, saltados=set(e["saltados"]),
            fuente_ok={tuple(x) for x in e["fuente_ok"]}, forzados=set(e["forzados"]),
            factor=1 + float(e.get("subir") or 0) / 100 if en_practica(e) else 1.0)
    return e, _cache["resultado"]


def ficha_proveedores():
    """Los proveedores guardados, para elegir en pantalla (y adivinar por el nombre del
    archivo de una lista, que igual se confirma a la vista)."""
    df = memoria.leer("proveedores")
    return [{"nombre": r.nombre, "alias": [a.strip() for a in str(r.alias).split("|") if a.strip()],
             "tipo": r.tipo, "iva": r.iva_lista, "margen": r.margen or "fijo"}
            for r in df.sort_values("nombre", key=lambda s: s.str.lower()).itertuples()]


# ---------- formato ----------
@app.template_filter("pesos")
def pesos(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if v != v:        # NaN
        return ""
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "$ " + (s[:-3] if s.endswith(",00") else s)


@app.template_filter("pct")
def pct(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if v != v:
        return ""
    return f"{v:+.0f}%".replace("+0%", "0%")


@app.template_filter("vacio")
def vacio(v):
    """'' para None/NaN, así las plantillas no muestran 'nan'."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return v


EXPLICACION = {
    "match a confirmar": "No estoy seguro de qué producto es.",
    "match dudoso": "No estoy seguro de qué producto es.",
    "sin match": "No encontré este producto en Odoo.",
    "variación fuerte": "El costo cambia más de un 30%.",
    "presentación distinta": "El proveedor cotiza otra presentación que la cargada en Odoo.",
    "no tenía costo cargado": "Este producto no tenía costo en Odoo: no hay con qué comparar.",
    "otro renglón matcheó el mismo producto con más confianza":
        "Otro renglón ya actualiza este mismo producto.",
    "": "Revisalo antes de importar.",
}


def tarjetas(res):
    """Convierte la hoja 'confirmar' en tarjetas para la pantalla."""
    out = []
    for r in res["confirmar"].to_dict("records"):
        alerta = r.get("alerta", "")
        tipo = "producto"
        if alerta in ("variación fuerte", "no tenía costo cargado"):
            tipo = "aprobar"
        elif alerta == "presentación distinta":
            tipo = "presentacion"
        elif alerta == "otra fuente":
            tipo = "fuente"
        elif alerta.startswith("otro renglón"):
            tipo = "duplicado"
        elif alerta == "falta IVA":
            tipo = "iva"
        opciones = list(r.get("opciones") or [])
        if r.get("referencia") and not any(o["referencia"] == r["referencia"] for o in opciones):
            opciones.insert(0, {"referencia": r["referencia"], "nombre": r.get("nombre_odoo", ""),
                                "costo_actual": r.get("costo_actual"), "score": r.get("score")})
        explicacion = EXPLICACION.get(alerta, alerta)
        if "cotiza por kilo" in str(r.get("via", "")):
            explicacion = "Cotiza por kilo, pero el nombre en Odoo no dice cuánto pesa."
        if alerta == "presentación distinta":
            explicacion = str(r.get("desajuste", "")).replace(
                "presentación distinta: ", "El proveedor cotiza otra presentación. ")
        if alerta == "otra fuente":
            explicacion = (f"Este producto se lo venís comprando a {r.get('fuente_habitual')}, "
                           f"y este precio es de {r.get('fuente')}.")
        if tipo in ("aprobar", "fuente", "presentacion") and r.get("estado") == "revisar":
            # la alerta del costo tapa que tampoco está seguro del producto: decirlo
            explicacion = "No estoy seguro de que sea este producto. " + explicacion
        if alerta == "falta IVA":
            incluido = bool(r.get("iva_incluido"))
            explicacion = ("La lista viene con IVA y tu costo va sin IVA: falta saber qué IVA sacarle a este producto."
                           if incluido else "La lista viene sin IVA: falta saber qué IVA lleva este producto.")
            perc = float(r.get("percepcion") or 0)
            base = float(r.get("precio_lista") or 0)
            sugerido = float(r.get("iva_sugerido") or 0.21)
            r["opciones_iva"] = [{"valor": v, "texto": t, "sugerido": abs(v - sugerido) < 1e-9,
                                  "costo": costo_de_lista(base, v, perc, incluido, memoria.costo_sin_iva())}
                                 for v, t in ((0.21, "21%"), (0.105, "10,5%"))]
        out.append({**r, "tipo_tarjeta": tipo, "explicacion": explicacion, "opciones": opciones,
                    "id_tarjeta": f"{r['clave_renglon']}-{r.get('referencia') or 'x'}"})
    # el mismo producto ofrecido por varios (el atún en dos listas) queda uno al lado del otro
    out.sort(key=lambda t: (not t.get("nombre_odoo"), str(t.get("nombre_odoo", "")).lower(), str(t.get("fuente", ""))))
    return out


def iva_por_proveedor(res):
    """Proveedores de los que no se sabe si cotizan con o sin IVA, con lo que mandaron:
    se pregunta una vez por proveedor, no por producto."""
    esp = res.get("esperan_iva")
    if esp is None or not len(esp):
        return []
    return [{"proveedor": prov, "cantidad": len(g), "ejemplos": g["linea"].astype(str).head(3).tolist(),
             "actual": memoria.iva_de(prov)}
            for prov, g in esp.groupby("proveedor_iva", sort=True)]


def pantalla_inicio(error=None, aviso=None, codigo=200):
    e = leer_estado()
    cat = catalogo(e) if e and e.get("export") else None
    return render_template("inicio.html", estado=e, cat=resumen(cat) if cat is not None else None,
                           config=leer_config(), memoria=memoria.conteos(),
                           proveedores=ficha_proveedores(), iva_opciones=IVA_OPCIONES,
                           error=error, aviso=aviso), codigo


# ---------- primera vez ----------
def falta_configurar():
    """Una instalación nueva: sin proveedores cargados y sin haber pasado por la bienvenida."""
    return not leer_config().get("configurado") and memoria.leer("proveedores").empty


@app.route("/bienvenida", methods=["GET", "POST"])
def bienvenida():
    """Cuatro pasos para un negocio que empieza: nombre, costo con o sin IVA, proveedores y
    (opcional) la planilla. También sirve para cambiarlos después."""
    if request.method == "GET":
        return render_template("bienvenida.html", config=leer_config(), error=None,
                               costo_sin_iva=memoria.costo_sin_iva())
    c = leer_config()
    c["negocio"] = request.form.get("negocio", "").strip()
    if request.form.get("costo_iva") in ("con", "sin"):
        memoria.fijar_costo_sin_iva(request.form["costo_iva"] == "sin")
        _cache["resultado"] = None
    lineas = request.form.get("proveedores", "").splitlines()
    archivo = request.files.get("archivo_proveedores")
    if archivo and archivo.filename:
        lineas += archivo.read().decode("utf-8-sig", errors="replace").splitlines()
    for linea in lineas:
        linea = " ".join(linea.split())
        if not linea:
            continue
        # "Cooperativa Agrícola del Sur - CAS": nombre y sigla
        nombre, _, alias = linea.partition(" - ")
        n = memoria.registrar_proveedor(nombre.strip(), nota="cargado en la bienvenida")
        if alias.strip():
            df = memoria.leer("proveedores")
            df.loc[df["nombre"] == n, "alias"] = alias.strip()
            memoria.guardar("proveedores", df)
    url = request.form.get("url", "").strip()
    if url:
        try:
            sheets.url_csv(url)
        except ErrorDeDatos as err:
            return render_template("bienvenida.html", config=c, error=str(err),
                                   costo_sin_iva=memoria.costo_sin_iva()), 400
        c["sheets_url"] = url
    c["configurado"] = True
    guardar_config(c)
    return redirect(url_for("inicio"))


# ---------- el negocio de ejemplo ----------
EJEMPLO = RAIZ / "ejemplo"
MODO_EJEMPLO = bool(os.environ.get("ACTUALIZADOR_EJEMPLO"))


@app.route("/ejemplo/cargar", methods=["POST"])
def cargar_ejemplo():
    """Solo con "Probar con el ejemplo": arma un ciclo con los archivos del almacén inventado."""
    if not MODO_EJEMPLO:
        abort(404)
    respaldar_memoria()
    if CICLO.exists():
        for p in CICLO.iterdir():
            p.unlink()
    e = estado_vacio()
    e["export"] = guardar_archivo("export_odoo.csv", (EJEMPLO / "export_odoo.csv").read_bytes())
    e["export_original"] = "export_odoo.csv (ejemplo)"
    e["export_cargado"] = ahora()
    e["fuentes"] = [
        {"id": "ej1", "tipo": "planilla", "nombre": "Planilla de mensajes (ejemplo)", "agregado": ahora(),
         "archivo": guardar_archivo("planilla_mensajes.csv", (EJEMPLO / "planilla_mensajes.csv").read_bytes())},
        *[{"id": f"ej{i}", "tipo": "lista", "proveedor": prov, "nombre": nombre, "agregado": ahora(),
           "archivo": guardar_archivo(nombre, (EJEMPLO / nombre).read_bytes()),
           "huella": huella(prov, (EJEMPLO / nombre).read_bytes().hex()[:20000])}
          for i, (prov, nombre) in enumerate((("Distribuidora Norte", "lista Distribuidora Norte.pdf"),
                                              ("Distribuidora Sur", "lista Distribuidora Sur.xlsx")), start=2)]]
    _cache["catalogo"] = None
    guardar_estado(e)
    return redirect(url_for("revisar"))


# ---------- inicio: armar el ciclo ----------
@app.route("/")
def inicio():
    if falta_configurar():
        return redirect(url_for("bienvenida"))
    return pantalla_inicio()


def respaldar_memoria(guardar=10):
    """Copia de lo aprendido al empezar cada ciclo (se guardan las últimas `guardar`)."""
    import shutil
    carpeta = DATOS / "respaldos"
    destino = carpeta / dt.datetime.now().strftime("%Y-%m-%d %H%M%S")
    shutil.copytree(DATOS / "memoria", destino, dirs_exist_ok=True)
    for viejo in sorted(p for p in carpeta.iterdir() if p.is_dir())[:-guardar]:
        shutil.rmtree(viejo, ignore_errors=True)


@app.route("/nuevo", methods=["POST"])
def nuevo():
    respaldar_memoria()
    if CICLO.exists():
        for p in CICLO.iterdir():
            p.unlink()
    _cache["catalogo"] = None
    e = estado_vacio()
    if request.form.get("practica"):
        e["practica"] = True
        try:
            e["subir"] = max(-50.0, min(100.0, float(str(request.form.get("subir") or "10").replace(",", "."))))
        except ValueError:
            e["subir"] = 10.0
    guardar_estado(e)
    return redirect(url_for("inicio"))


def guardar_archivo(nombre, contenido):
    CICLO.mkdir(parents=True, exist_ok=True)
    destino = f"{uuid.uuid4().hex[:6]}_{Path(nombre).name}"
    (CICLO / destino).write_bytes(contenido)
    return destino


def validar_export(f, quitar_repetidos=True):
    suf = Path(f.filename).suffix.lower()
    if suf not in (".csv", ".xlsx", ".xls"):
        raise ErrorDeDatos("El export de Odoo tiene que ser un archivo .csv o .xlsx.")
    contenido = f.read()
    tmp = DATOS / f"_subida{suf}"
    DATOS.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(contenido)
    try:
        return construir(tmp, quitar_repetidos=quitar_repetidos,
                         no_son_productos=memoria.no_son_productos()), contenido
    finally:
        tmp.unlink(missing_ok=True)


@app.route("/export", methods=["POST"])
def subir_export():
    f = request.files.get("export")
    try:
        if not f or not f.filename:
            raise ErrorDeDatos("Elegí el archivo exportado de Odoo.")
        cat, contenido = validar_export(f)
    except ErrorDeDatos as err:
        return pantalla_inicio(error=str(err), codigo=400)
    e = estado_o_nuevo()
    if e.get("export"):
        (CICLO / e["export"]).unlink(missing_ok=True)
    e["export"] = guardar_archivo("export_odoo" + Path(f.filename).suffix.lower(), contenido)
    e["export_original"] = f.filename
    e["export_cargado"] = ahora()
    _cache["catalogo"] = cat
    guardar_estado(e)
    return redirect(url_for("inicio"))


def agregar_fuente(f):
    e = estado_o_nuevo()
    f.setdefault("id", uuid.uuid4().hex[:8])
    f.setdefault("agregado", ahora())
    e["fuentes"].append(f)
    guardar_estado(e)


@app.route("/fuente/sheets", methods=["POST"])
def fuente_sheets():
    url = request.form.get("url", "").strip() or leer_config().get("sheets_url", "")
    try:
        if not url:
            raise ErrorDeDatos("Pegá el link de la planilla de Google Sheets.")
        libro = sheets.leer_libro(url)
    except ErrorDeDatos as err:
        return pantalla_inicio(error=str(err), codigo=400)
    c = leer_config()
    c["sheets_url"] = url
    guardar_config(c)
    # la planilla también dice cómo manda precios cada proveedor: queda en la ficha
    for fila in pd.read_csv(io.BytesIO(libro["mensajes"]), dtype=str,
                            keep_default_na=False).iloc[:, 0]:
        if str(fila).strip():
            memoria.registrar_proveedor(fila, tipo="mensaje")
    for p in libro["listas"]:
        memoria.registrar_proveedor(p, tipo="lista")
    for p, _ in libro["manual"]:
        memoria.registrar_proveedor(p, tipo="manual")
    e = estado_o_nuevo()
    e["fuentes"] = [f for f in e["fuentes"] if f["tipo"] != "planilla" or not f.get("de_sheets")]
    guardar_estado(e)
    agregar_fuente({"tipo": "planilla", "de_sheets": True,
                    "nombre": f"Planilla de Google Sheets (leída {ahora()})",
                    "archivo": guardar_archivo("planilla_sheets.csv", libro["mensajes"]),
                    "listas_esperadas": libro["listas"], "manual": libro["manual"]})
    return redirect(url_for("inicio"))


@app.route("/iva_proveedor", methods=["POST"])
def iva_proveedor():
    """Cómo cotiza un proveedor (con o sin IVA): queda en su ficha y vale para todo lo que mande."""
    prov, iva = request.form.get("proveedor", "").strip(), request.form.get("iva", "")
    if prov and iva in IVA_OPCIONES:
        memoria.fijar_iva(prov, iva)
        _cache["resultado"] = None
    return redirect(url_for("revisar"))


@app.route("/fuente/mensaje", methods=["POST"])
def fuente_mensaje():
    prov = request.form.get("proveedor", "").strip()
    texto = request.form.get("texto", "").strip()
    iva = request.form.get("iva", "")
    if not prov or not texto:
        return pantalla_inicio(error="Para un mensaje suelto hacen falta el proveedor y el mensaje.", codigo=400)
    if iva not in IVA_OPCIONES and not memoria.sabe_iva(prov):
        return pantalla_inicio(error=f"Falta decir si los precios del mensaje de {prov} incluyen IVA. "
                                     "Queda guardado y no se vuelve a preguntar.", codigo=400)
    conocido = memoria.buscar_proveedor(prov)
    prov = memoria.registrar_proveedor(prov, tipo="mensaje")
    if iva in IVA_OPCIONES:
        memoria.fijar_iva(prov, iva)
    agregar_fuente({"tipo": "mensaje", "proveedor": prov, "texto": texto,
                    "nombre": f"Mensaje de {prov} ({ahora()})"})
    if not conocido:
        return pantalla_inicio(aviso=f"Proveedor nuevo guardado: {prov}. Desde ahora aparece en la lista para elegir.")
    return redirect(url_for("inicio"))


@app.route("/fuente/lista", methods=["POST"])
@app.route("/fuente/listas", methods=["POST"])
def fuente_lista():
    """Una o varias listas: cada archivo con el proveedor elegido en pantalla. El
    proveedor se elige, no se adivina: así se sabe de antemano de quién es cada precio."""
    archivos = [f for f in request.files.getlist("listas") + request.files.getlist("lista") if f and f.filename]
    provs = request.form.getlist("proveedor")
    ivas = request.form.getlist("iva")
    e = estado_o_nuevo()
    idx = indice_proveedores(catalogo(e), memoria.leer("proveedor_alias")) if catalogo(e) is not None else None
    listas, nuevos = [], []
    try:
        if not archivos:
            raise ErrorDeDatos("Arrastrá al menos una lista (PDF, Excel o CSV).")
        for i, f in enumerate(archivos):
            prov = (provs[i] if i < len(provs) else "").strip()
            iva = ivas[i] if i < len(ivas) else ""
            if not prov:
                raise ErrorDeDatos(f"Elegí de qué proveedor es {f.filename}.")
            if not iva and not memoria.sabe_iva(prov):
                raise ErrorDeDatos(f"Para {prov} falta decir si los precios de la lista incluyen IVA. "
                                   "Se pregunta una sola vez.")
            contenido = f.read()
            articulos, no_leidos = leer_lista(f.filename, contenido, idx)
            listas.append((f.filename, prov, iva, contenido, articulos, no_leidos))
    except ErrorDeDatos as err:
        return pantalla_inicio(error=str(err), codigo=400)
    for nombre, prov, iva, contenido, articulos, no_leidos in listas:
        conocido = memoria.buscar_proveedor(prov)
        prov = memoria.registrar_proveedor(prov, tipo="lista")
        if not conocido:
            nuevos.append(prov)
        if iva:
            memoria.fijar_iva(prov, iva)
        agregar_fuente({"tipo": "lista", "proveedor": prov, "nombre": nombre,
                        "archivo": guardar_archivo(nombre, contenido),
                        "huella": huella(prov, contenido.hex()[:20000])})
    if nuevos:
        return pantalla_inicio(aviso=f"Proveedor nuevo guardado: {', '.join(nuevos)}. "
                                     "Desde ahora aparece en la lista para elegir.")
    return redirect(url_for("inicio"))


@app.route("/fuente/archivo", methods=["POST"])
def fuente_archivo():
    archivos = [f for f in request.files.getlist("archivos") if f and f.filename]
    if not archivos:
        return pantalla_inicio(error="Elegí al menos un archivo.", codigo=400)
    try:
        for f in archivos:
            contenido = f.read()
            fuente = fuente_de_archivo(f.filename, contenido)
            if fuente["tipo"] == "lista":
                raise ErrorDeDatos(f"{f.filename} parece una lista de precios: subila en "
                                   "\"Lista de una distribuidora\" para indicar de quién es.")
            fuente.pop("contenido")
            fuente["archivo"] = guardar_archivo(f.filename, contenido)
            agregar_fuente(fuente)
    except ErrorDeDatos as err:
        return pantalla_inicio(error=str(err), codigo=400)
    return redirect(url_for("inicio"))


@app.route("/fuente/texto", methods=["POST"])
def fuente_texto():
    texto = request.form.get("texto", "").strip()
    if not texto:
        return pantalla_inicio(error="El cuadro de texto está vacío.", codigo=400)
    agregar_fuente({"tipo": "texto", "texto": texto, "nombre": f"Texto pegado ({ahora()})"})
    return redirect(url_for("inicio"))


@app.route("/fuente/quitar", methods=["POST"])
def quitar_fuente():
    e = estado_o_nuevo()
    i = request.form["id"]
    for f in [f for f in e["fuentes"] if f["id"] == i]:
        if f.get("archivo"):
            (CICLO / f["archivo"]).unlink(missing_ok=True)
    e["fuentes"] = [f for f in e["fuentes"] if f["id"] != i]
    guardar_estado(e)
    return redirect(url_for("inicio"))


@app.route("/cargar", methods=["POST"])
def cargar():
    """Atajo: ciclo nuevo con export + archivos de mensajes en un solo paso."""
    export = request.files.get("export")
    mensajes = [f for f in request.files.getlist("mensajes") if f and f.filename]
    try:
        if not export or not export.filename:
            raise ErrorDeDatos("Falta el export de productos de Odoo.")
        cat, contenido_export = validar_export(export)
        fuentes = []
        for f in mensajes:
            c = f.read()
            fu = fuente_de_archivo(f.filename, c)
            fu.pop("contenido")
            fuentes.append((fu, f.filename, c))
    except ErrorDeDatos as err:
        return pantalla_inicio(error=str(err), codigo=400)
    nuevo()
    e = estado_vacio()
    e["export"] = guardar_archivo("export_odoo" + Path(export.filename).suffix.lower(), contenido_export)
    e["export_original"] = export.filename
    e["export_cargado"] = ahora()
    for fu, nombre, c in fuentes:
        fu.update({"id": uuid.uuid4().hex[:8], "agregado": ahora(), "archivo": guardar_archivo(nombre, c)})
        e["fuentes"].append(fu)
    _cache["catalogo"] = cat
    guardar_estado(e)
    return redirect(url_for("revisar"))


# ---------- revisión ----------
@app.route("/revisar")
def revisar():
    try:
        e, res = resultado()
    except ErrorDeDatos as err:
        return pantalla_inicio(error=str(err), codigo=400)
    if res is None:
        return redirect(url_for("inicio"))
    cat = catalogo(e)
    av = res["avisos"]
    faltantes = av[av.tipo.eq("faltante")] if len(av) else av
    igual = av[av.tipo.eq("sin cambio")] if len(av) else av
    rot = res["rotacion"]
    fuera = res["fuera_de_catalogo"]
    no_encontrado = request.args.get("no_encontrado", "")
    error = (f"No encontré “{no_encontrado}” en tu catálogo de Odoo, así que no se guardó nada. "
             "Elegí el producto de las sugerencias que aparecen mientras escribís: así queda "
             "el nombre exacto.") if no_encontrado else None
    return render_template(
        "revisar.html", estado=e, cat=resumen(cat), fuentes=res["fuentes"], error=error,
        tarjetas=tarjetas(res), iva_proveedores=iva_por_proveedor(res), iva_opciones=IVA_OPCIONES,
        listos=res["listos"].to_dict("records"),
        sin_cambio=len(res["sin_cambio"]),
        saltados=res["saltados"].to_dict("records"),
        ignorados=res["ignorados"].to_dict("records"),
        faltantes=faltantes.to_dict("records"),
        igual=igual.to_dict("records"),
        notas=res["notas"].to_dict("records"),
        rotacion=rot[rot.accion != "sin cambio"].to_dict("records") if len(rot) else [],
        no_mencionados=res["no_mencionados"].to_dict("records"),
        fuera=fuera.to_dict("records") if len(fuera) else [],
        productos=sorted(cat["nombre_completo"].tolist(), key=str.lower))


@app.route("/decidir", methods=["POST"])
def decidir():
    e, res = resultado()
    if res is None:
        return redirect(url_for("inicio"))
    clave = request.form["clave"]
    accion = request.form["accion"]
    ref_actual = request.form.get("referencia", "")

    if accion == "forzar":            # "analizar igual" un mensaje ya procesado
        e["forzados"].append(clave)
        guardar_estado(e)
        return redirect(url_for("revisar"))

    todas = res["analisis"]
    fila = todas[todas.clave_renglon == clave]
    if not len(fila) and accion != "restaurar":
        return redirect(url_for("revisar"))
    r = fila.iloc[0].to_dict() if len(fila) else {}
    proveedor = r.get("proveedor") or r.get("proveedor_bloque") or ""
    texto = r.get("descripcion", "")
    es_lista = bool(r.get("es_lista")) and r.get("es_lista") == r.get("es_lista")
    distribuidor = r.get("proveedor_bloque", "")
    codigo = r.get("codigo", "")

    def aprender(ref):
        if es_lista and codigo:
            memoria.aprender_codigo(distribuidor, codigo, texto, ref)
        else:
            memoria.aprender_alias(texto, ref, proveedor,
                                   nota=f"confirmado en pantalla {dt.date.today().isoformat()}")

    def confirmar(ref):
        """Las tarjetas con botón verde muestran el producto y el cambio de costo: apretarlo
        es decir "este producto, este costo". Si la app no estaba segura del producto (lo
        matcheó por nombre), queda aprendido; si no, la tarjeta volvía a aparecer con otra
        pregunta ("Revisalo antes de importar") y parecía que el botón no hacía nada."""
        if ref and r.get("estado") == "revisar":
            aprender(ref)
        e["aprobados"].append([clave, ref])

    if accion == "elegir":
        ref = request.form.get("elegido", "")
        buscado = request.form.get("buscado", "").strip()
        if buscado:          # eligió con el buscador: se busca la referencia por nombre
            ref = producto_por_nombre(catalogo(e), buscado)
            if not ref:
                # NO caer en la opción marcada: es la sugerencia que la persona está corrigiendo
                return redirect(url_for("revisar", no_encontrado=buscado) + "#confirmar")
        if ref:
            aprender(ref)
            # vio el costo actual y el nuevo al elegir: la variación queda aprobada para este ciclo
            e["aprobados"].append([clave, ref])
            e["fuente_ok"].append([clave, ref])
    elif accion == "aprobar":
        confirmar(ref_actual)
    elif accion == "fuente_ok":
        confirmar(ref_actual)
        e["fuente_ok"].append([clave, ref_actual])
    elif accion == "bulto":           # la lista cotiza el bulto cerrado: se divide, y queda aprendido
        n = int(request.form.get("unidades") or 0)
        if n > 1 and codigo and ref_actual:
            memoria.aprender_codigo(distribuidor, codigo, texto, ref_actual, unidades=n)
            e["aprobados"].append([clave, ref_actual])
    elif accion == "iva_producto":    # qué IVA lleva este producto: se recuerda para siempre
        try:
            iva = float(request.form.get("iva", ""))
        except ValueError:
            iva = None
        if iva in (0.21, 0.105) and ref_actual:
            memoria.fijar_iva_producto(ref_actual, iva, float(r.get("percepcion") or 0),
                                       nota=f"{r.get('nombre_odoo', '')} · confirmado {dt.date.today().isoformat()}")
    elif accion == "preferir":        # dos renglones para el mismo producto: usar este
        otro = request.form.get("otro", "")
        if otro:
            e["saltados"].append(otro)
        e["saltados"] = [s for s in e["saltados"] if s != clave]
    elif accion == "gramaje":
        memoria.aprobar(ref_actual, "gramaje", f"{texto} ({proveedor}): cambia la presentación")
        confirmar(ref_actual)
    elif accion == "presentacion":
        memoria.aprobar(ref_actual, "presentacion", f"{texto} ({proveedor}): mismo producto, el nombre queda")
        confirmar(ref_actual)
    elif accion == "ignorar":
        if es_lista and codigo:
            memoria.aprender_codigo(distribuidor, codigo, texto, "")
        else:
            memoria.aprender_ignorar(texto, proveedor, request.form.get("motivo") or "no lo vendemos")
    elif accion == "saltar":
        e["saltados"].append(clave)
    elif accion == "restaurar":
        e["saltados"] = [s for s in e["saltados"] if s != clave]
    guardar_estado(e)
    return redirect(url_for("revisar") + "#confirmar")


# ---------- generar y verificar ----------
@app.route("/generar", methods=["POST"])
def generar():
    e, res = resultado()
    if res is None:
        return redirect(url_for("inicio"))
    SALIDAS.mkdir(parents=True, exist_ok=True)
    marca = dt.datetime.now().strftime("%Y-%m-%d %H%M")
    if en_practica(e):
        return generar_practica(e, res, marca)
    archivos, error = {}, None
    try:
        archivos["import"] = escribir_import(res, SALIDAS / f"IMPORTAR costos {marca}.xlsx")
        archivos["reporte"] = escribir_reporte(res, SALIDAS / f"Reporte {marca}.xlsx", memoria.margen_variable())
        archivos["rotacion"] = escribir_rotacion(res, SALIDAS / f"Publicacion rotaciones {marca}.xlsx")
    except ErrorDeDatos as err:
        error = str(err)
        archivos.pop("import", None)
    archivos = {k: Path(v).name for k, v in archivos.items() if v}

    if archivos.get("import"):
        fecha = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        # queda registrado qué se mandó y de dónde salió cada costo
        memoria.registrar_historial(filas_historial(res, fecha))
        for h in res["huellas"]:
            memoria.marcar_procesado(h["fuente"], h["huella"], h["origen"], fecha[:10])
        tabla_import(res).to_json(CICLO / "ultimo_import.json", orient="records", force_ascii=False)
        e["generado"] = {"archivo": archivos["import"], "fecha": fecha}
        # "ya procesado" vale para los ciclos que vienen, no para este: si se vuelve a la
        # revisión, lo generado tiene que seguir ahí
        e["forzados"] = sorted(set(e["forzados"]) | {h["huella"] for h in res["huellas"]})
        guardar_estado(e)
        _cache["resultado"] = res
    return mostrar_generado(archivos, error)


def generar_practica(e, res, marca):
    """En práctica se genera todo para mirarlo, pero con otro nombre y sin registrar nada:
    ni historial, ni mensajes procesados, ni el archivo que se verifica después."""
    carpeta = SALIDAS / "practica"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos, error = {}, None
    try:
        archivos["import"] = escribir_import(res, carpeta / f"PRACTICA - NO IMPORTAR {marca}.xlsx")
        archivos["reporte"] = escribir_reporte(res, carpeta / f"PRACTICA - Reporte {marca}.xlsx",
                                               memoria.margen_variable())
    except ErrorDeDatos as err:
        error = str(err)
        archivos.pop("import", None)
    archivos = {k: "practica/" + Path(v).name for k, v in archivos.items() if v}
    return mostrar_generado(archivos, error)


def mostrar_generado(archivos, error=None, verificacion=None):
    e, res = resultado()
    ctl = control(res, memoria.margen_variable()) if res is not None else pd.DataFrame()
    return render_template("generado.html", archivos=archivos, error=error,
                           listos=len(res["listos"]) if res is not None else 0,
                           pendientes=len(res["confirmar"]) + len(res["esperan_iva"]) if res is not None else 0,
                           control=ctl.to_dict("records") if len(ctl) else [],
                           a_revisar=int(ctl["revisar"].ne("").sum()) if len(ctl) else 0,
                           verificacion=verificacion)


@app.route("/generado")
def ver_generado():
    e = leer_estado()
    if not e or not e.get("generado"):
        return redirect(url_for("revisar"))
    return mostrar_generado({"import": e["generado"]["archivo"]})


@app.route("/verificar", methods=["POST"])
def verificar_import():
    e = leer_estado()
    f = request.files.get("export")
    if not e or en_practica(e) or not (CICLO / "ultimo_import.json").exists():
        return redirect(url_for("inicio"))
    archivos = {"import": e.get("generado", {}).get("archivo", "")}
    try:
        if not f or not f.filename:
            raise ErrorDeDatos("Elegí el export nuevo de Odoo (el que bajaste después de importar).")
        despues, _ = validar_export(f, quitar_repetidos=False)
    except ErrorDeDatos as err:
        return mostrar_generado(archivos, error=str(err))
    esperado = pd.read_json(CICLO / "ultimo_import.json", orient="records", dtype={"default_code": str})
    antes = catalogo(e)
    v = verificar(esperado, antes, despues)
    # el historial guarda si el costo realmente quedó en Odoo. Se cruza por ID externo:
    # la Referencia interna puede estar vacía en muchos productos a la vez
    h = memoria.leer("historial")
    fecha = e.get("generado", {}).get("fecha", "")
    estados = dict(zip(v["control"]["id"], v["control"]["estado"]))
    id_de = dict(zip(antes["referencia"], antes["id_externo"]))
    mask = (h["fecha"] == fecha) & (h["estado"] == "generado")
    h.loc[mask, "estado"] = h.loc[mask, "referencia"].map(
        lambda r: "verificado" if estados.get(id_de.get(r)) == "ok" else "no se aplicó")
    memoria.guardar("historial", h)
    return mostrar_generado(archivos, verificacion=v)


@app.route("/salidas/<path:nombre>")
def descargar(nombre):
    ruta = (SALIDAS / nombre).resolve()
    if SALIDAS.resolve() not in ruta.parents or not ruta.is_file():
        abort(404)
    return send_from_directory(SALIDAS, nombre, as_attachment=True)


# ---------- memoria ----------
TABLAS_EDITABLES = ("alias", "codigos_proveedor", "ignorar", "aprobados", "abreviaturas", "no_son_productos")
# las que se pueden cargar a mano desde la pantalla (las demás se aprenden confirmando)
TABLAS_AGREGABLES = {"abreviaturas": ("texto", "significa"), "no_son_productos": ("empieza_con",),
                     "codigos_proveedor": ("proveedor", "codigo_proveedor", "descripcion_proveedor", "buscado")}


@app.route("/memoria/agregar", methods=["POST"])
def agregar_memoria():
    nombre = request.form.get("tabla", "")
    if nombre not in TABLAS_AGREGABLES:
        abort(400)
    campos = {c: request.form.get(c, "").strip() for c in TABLAS_AGREGABLES[nombre]}
    if nombre == "codigos_proveedor":
        if campos["proveedor"] and campos["codigo_proveedor"] and campos["buscado"]:
            ref = producto_por_nombre(catalogo(leer_estado()), campos["buscado"])
            if ref:
                memoria.aprender_codigo(campos["proveedor"], campos["codigo_proveedor"],
                                        campos["descripcion_proveedor"], ref)
    elif campos[TABLAS_AGREGABLES[nombre][0]]:
        campos["nota"] = request.form.get("nota", "").strip()
        memoria.agregar(nombre, campos)
    _cache["resultado"] = None
    if nombre == "no_son_productos":
        _cache["catalogo"] = None          # cambia qué filas del export se toman como productos
    return redirect(url_for("ver_memoria") + f"#{nombre}")


@app.route("/memoria")
def ver_memoria():
    tablas = {n: memoria.leer(n).reset_index().to_dict("records") for n in TABLAS_EDITABLES}
    hist = memoria.leer("historial")
    cat = catalogo(leer_estado())
    return render_template("memoria.html", tablas=tablas, conteos=memoria.conteos(),
                           proveedores=ficha_proveedores(), iva_opciones=IVA_OPCIONES,
                           costo_sin_iva=memoria.costo_sin_iva(),
                           abrir_proveedores=request.args.get("abrir") == "proveedores",
                           guardado=bool(request.args.get("guardado")),
                           historial=hist.tail(300).iloc[::-1].to_dict("records"),
                           productos=sorted(cat["nombre_completo"].tolist(), key=str.lower) if cat is not None else [])


@app.route("/proveedores", methods=["POST"])
def editar_proveedor():
    """Agregar un proveedor nuevo desde Lo aprendido."""
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        memoria.registrar_proveedor(nombre, tipo=request.form.get("tipo", ""))
    return redirect(url_for("ver_memoria", abrir="proveedores") + "#proveedores")


@app.route("/proveedores/todos", methods=["POST"])
def editar_proveedores():
    """Todas las filas de la tabla de proveedores en un solo guardado: solo se toca lo que cambió."""
    campos = {c: request.form.getlist(c) for c in ("nombre", "alias", "tipo", "iva", "margen")}
    df = memoria.leer("proveedores")
    cambios_iva = []
    for i, nombre in enumerate(campos["nombre"]):
        fila = df["nombre"] == nombre
        if not fila.any():
            continue
        for col, campo in (("alias", "alias"), ("tipo", "tipo"), ("margen", "margen")):
            if i < len(campos[campo]):
                df.loc[fila, col] = campos[campo][i].strip()
        iva = campos["iva"][i] if i < len(campos["iva"]) else ""
        if iva != df.loc[fila, "iva_lista"].iloc[0]:
            cambios_iva.append((nombre, iva))
    memoria.guardar("proveedores", df)
    for nombre, iva in cambios_iva:          # el IVA también se refleja en impuestos.csv
        if iva:
            memoria.fijar_iva(nombre, iva)
    _cache["resultado"] = None
    return redirect(url_for("ver_memoria", abrir="proveedores", guardado=1) + "#proveedores")


@app.route("/memoria/borrar", methods=["POST"])
def borrar_memoria():
    nombre = request.form["tabla"]
    if nombre not in TABLAS_EDITABLES:
        abort(400)
    df = memoria.leer(nombre)
    i = int(request.form["fila"])
    if 0 <= i < len(df):
        memoria.guardar(nombre, df.drop(index=i))
    _cache["resultado"] = None
    if nombre == "no_son_productos":
        _cache["catalogo"] = None
    return redirect(url_for("ver_memoria") + f"#{nombre}")


@app.route("/ayuda")
def ayuda():
    return render_template("ayuda.html")


def main():
    import logging
    import socket
    import flask.cli
    # la ventana negra la ve alguien no técnico: sin avisos de servidor de desarrollo
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    flask.cli.show_server_banner = lambda *a, **k: None

    url = f"http://127.0.0.1:{PUERTO}"
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PUERTO)) == 0:
            # ya estaba abierta (doble clic otra vez): solo mostrarla
            webbrowser.open(url)
            print("\n  La app ya estaba abierta. Se abrio en el navegador.\n")
            return
    if not os.environ.get("ACTUALIZADOR_SIN_NAVEGADOR"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  Actualizador de costos abierto en {url}\n"
          "  Si no se abrio el navegador, copia esa direccion en Chrome.\n"
          "  Para cerrar la app, cerra esta ventana.\n")
    app.run(host="127.0.0.1", port=PUERTO, debug=False)


if __name__ == "__main__":
    main()
