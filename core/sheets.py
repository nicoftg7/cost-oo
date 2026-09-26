"""Leer la planilla de seguimiento directo de Google Sheets, sin bajarla a mano.

Usa el link de exportación a CSV de Google: no necesita credenciales, pero la planilla
tiene que estar compartida como "Cualquier persona con el enlace: Lector".
"""
import re
import urllib.error
import urllib.request

from . import ErrorDeDatos

NO_COMPARTIDA = (
    "No pude leer la planilla de Google Sheets: Google pidió iniciar sesión.\n"
    "En la planilla: Compartir → Acceso general → \"Cualquier persona con el enlace\" → Lector.")


def url_csv(url):
    """Link de la planilla (el que se copia del navegador) -> link de descarga en CSV.
    Respeta la pestaña (gid) que estaba abierta al copiar el link."""
    url = url.strip()
    m = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        raise ErrorDeDatos("Ese link no parece de una planilla de Google Sheets. Copialo de la "
                           "barra del navegador con la planilla abierta.")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            + (f"&gid={gid.group(1)}" if gid else ""))


def pestanas(url, timeout=20, abrir=urllib.request.urlopen):
    """[(nombre, gid)] de todas las pestañas, o [] si no se pudieron averiguar."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        return []
    try:
        with abrir(f"https://docs.google.com/spreadsheets/d/{m.group(1)}/htmlview", timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return []
    vistos, salida = set(), []
    for nombre, gid in re.findall(r'\{name:\s*"([^"]+)",\s*pageUrl[^}]*?gid=(\d+)', html):
        if gid not in vistos:
            vistos.add(gid)
            salida.append((nombre, gid))
    return salida


def con_gid(url, gid):
    return re.sub(r"[#&?]gid=\d+", "", url.split("#")[0]) + f"#gid={gid}"


def leer_libro(url, abrir=urllib.request.urlopen):
    """Lee todas las pestañas de la planilla y las reconoce por sus columnas:
      - Proveedor + Mensaje           -> los mensajes a analizar
      - Proveedor + "Nos envía"/Lista -> proveedores que mandan lista (se avisa cuál falta)
      - Proveedor + Chequeado (sin Mensaje) -> proveedores que se actualizan a mano
    Devuelve {"mensajes": bytes|None, "listas": [...], "manual": [(proveedor, chequeado)],
              "pestanas": [nombres]}."""
    import io
    import pandas as pd
    from .normalize import sin_acentos
    tabs = pestanas(url, abrir=abrir) or [("", None)]
    libro = {"mensajes": None, "listas": [], "manual": [], "pestanas": []}
    for nombre, gid in tabs:
        contenido = descargar(con_gid(url, gid) if gid else url, abrir=abrir)
        try:
            df = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except Exception:
            continue
        cols = {sin_acentos(str(c)).lower(): c for c in df.columns}
        c_prov = next((c for n, c in cols.items() if "proveedor" in n), None)
        if not c_prov:
            continue
        libro["pestanas"].append(nombre)
        provs = [p.strip() for p in df[c_prov] if str(p).strip()]
        if any("mensaje" in n for n in cols):
            if libro["mensajes"] is None:
                libro["mensajes"] = contenido
        elif any("envia" in n or "lista" in n for n in cols):
            libro["listas"] += provs
        elif any("chequeado" in n for n in cols):
            c_chk = next(c for n, c in cols.items() if "chequeado" in n)
            libro["manual"] += [(str(p).strip(), str(ch).strip().upper() in ("TRUE", "VERDADERO", "SI", "SÍ", "1", "X"))
                                for p, ch in zip(df[c_prov], df[c_chk]) if str(p).strip()]
    if libro["mensajes"] is None:
        raise ErrorDeDatos("En la planilla no encontré una pestaña con las columnas Proveedor y Mensaje.")
    return libro


def descargar(url, timeout=20, abrir=urllib.request.urlopen):
    """Devuelve los bytes del CSV. `abrir` se puede reemplazar en los tests."""
    try:
        with abrir(url_csv(url), timeout=timeout) as r:
            contenido = r.read()
            tipo = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ErrorDeDatos(NO_COMPARTIDA)
        raise ErrorDeDatos(f"Google Sheets respondió con un error ({e.code}). Probá de nuevo en un rato.")
    except urllib.error.URLError as e:
        raise ErrorDeDatos(f"No me pude conectar a Google Sheets ({e.reason}). ¿Hay internet?")
    if "html" in tipo or contenido.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        raise ErrorDeDatos(NO_COMPARTIDA)
    return contenido
