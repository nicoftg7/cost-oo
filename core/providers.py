"""Reconocer proveedores: nombre en Odoo, marcas y apodos."""
import re

from rapidfuzz import fuzz, process

from .normalize import normalizar, sin_acentos


def indice_proveedores(catalogo, apodos=None):
    """clave normalizada -> nombre de proveedor canónico (incluye marcas y apodos)."""
    idx = {}
    for prov, marcas in zip(catalogo["proveedor"], catalogo["marcas"]):
        for m in [prov] + str(marcas or "").split(" | "):
            k = normalizar(m)
            if k:
                idx.setdefault(k, prov)
    if apodos is not None:
        for alias, prov in zip(apodos["alias"], apodos["proveedor"]):
            k = normalizar(alias)
            if k and normalizar(prov) in idx:
                idx[k] = idx[normalizar(prov)]
    return idx


def es_proveedor(texto, idx, corte=90):
    t = re.sub(r"\(.*?\)", " ", texto)          # "EPA (de ahora en más, EPA)"
    t = re.sub(r"\bde ahora en mas\b.*", "", sin_acentos(t).lower())
    k = normalizar(t)
    if not k or len(k) < 3:
        return None
    if k in idx:
        return idx[k]
    hit = process.extractOne(k, list(idx), scorer=fuzz.token_set_ratio, score_cutoff=corte)
    return idx[hit[0]] if hit else None


def mismo_proveedor(a, b, corte=88):
    return bool(a) and bool(b) and fuzz.token_set_ratio(normalizar(a), normalizar(b)) >= corte
