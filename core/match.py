"""Cruza cada renglón contra el catálogo: alias aprendido -> código -> nombre (fuzzy).

Las dos trampas del fuzzy matching que ya costaron archivos mal:
  1. Los alias se cruzaban entre proveedores ("Prepizzas" de uno le metía $5.700 a
     otro donde iban $2.100). Los alias van acotados a su proveedor.
  2. token_set da 100 cuando un texto es subconjunto de otro ("c/miel" se comía a
     "c/miel y cacao"). Se exige además token_sort >= 85 sobre el texto completo.
"""
import re

import pandas as pd
from rapidfuzz import fuzz, process

from .memory import clave_codigo
from .normalize import desajuste_tamano, expandir, normalizar
from .parse import variantes_texto
from .providers import indice_proveedores, mismo_proveedor
from . import transform

UMBRAL_AUTO = 88      # desde acá el match entra solo
UMBRAL_DUDOSO = 62    # debajo de esto se considera que no está en Odoo


def puntaje(a, b, **kw):
    """token_set solo premia subconjuntos; token_sort penaliza sobrantes. Mezclarlos desempata."""
    return 0.55 * fuzz.token_set_ratio(a, b) + 0.45 * fuzz.token_sort_ratio(a, b)


CODIGO = re.compile(r"\b([A-Z]{2,4})\s?-?\s?(\d{3,6})\b")


class Contexto:
    """Catálogo + memorias listos para matchear, con los cálculos caros cacheados."""

    def __init__(self, catalogo, memoria):
        memoria.cargar_abreviaturas()        # antes que nada: cambia cómo se leen los textos
        self.catalogo = catalogo
        self.alias = memoria.alias()
        self.ignorar = memoria.ignorar()
        self.reglas = memoria.leer("reglas_proveedor")
        self.impuestos = memoria.impuestos()
        self.costo_sin_iva = memoria.costo_sin_iva()
        self.idx = indice_proveedores(catalogo, memoria.leer("proveedor_alias"))
        self._sub = {}
        self._alias_sub = {}
        self._por_ref = {str(r).strip(): i for i, r in enumerate(catalogo["referencia"]) if str(r).strip()}
        self.memoria = memoria
        # marcas y proveedores del catálogo como conjuntos de palabras, para reconocer
        # "ATUN BAHIA LOMITO" como un producto de la marca Bahía
        self._marcas = [(set(k.split()), p) for k, p in self.idx.items() if len(k) >= 4]

    # ---- subconjuntos por proveedor ----
    def subconjunto(self, proveedor, corte=88):
        """Filas del proveedor, tolerando variantes de escritura del nombre en Odoo.
        Si no encuentra ninguna, devuelve el catálogo entero."""
        if not proveedor:
            return self.catalogo
        if proveedor not in self._sub:
            objetivo = normalizar(proveedor)
            def coincide(prov, marcas):
                nombres = [prov] + str(marcas or "").split(" | ")
                return any(fuzz.token_set_ratio(objetivo, normalizar(n)) >= corte for n in nombres if n)
            mask = [coincide(p, m) for p, m in zip(self.catalogo["proveedor"], self.catalogo["marcas"])]
            f = self.catalogo[mask]
            self._sub[proveedor] = f if len(f) else self.catalogo
        return self._sub[proveedor]

    def alias_de(self, proveedor):
        """Alias ACOTADOS A SU PROVEEDOR: "c/miel" de un proveedor no puede resolverse
        con el alias "mieles x 2 kg" aprendido de otro. Solo los alias sin proveedor
        cargado valen para cualquiera."""
        if proveedor not in self._alias_sub:
            a = self.alias
            if proveedor and len(a):
                propio = a[a["proveedor"].map(lambda p: mismo_proveedor(proveedor, p))]
                sub = propio if len(propio) else a[a["proveedor"].astype(str).str.strip() == ""]
            else:
                sub = a
            self._alias_sub[proveedor] = sub
        return self._alias_sub[proveedor]

    def por_referencia(self, ref):
        i = self._por_ref.get(str(ref).strip())
        return None if i is None else self.catalogo.iloc[i]

    # ---- estrategias de matcheo ----
    def todos_los_que_aplican(self, texto, proveedor="", corte=80, tope=8):
        """Para 'sin salames' o 'hamburguesas igual': devuelve TODOS los productos del
        proveedor que caen bajo ese término, no solo el mejor."""
        clave = normalizar(expandir(texto))
        if not clave or len(clave) < 3:
            return []
        sub = self.subconjunto(proveedor)
        return [(sub.iloc[h[2]], h[1]) for h in
                process.extract(clave, sub["clave"].tolist(), scorer=fuzz.token_set_ratio, limit=tope)
                if h[1] >= corte]

    def candidatos(self, texto, proveedor="", n=3):
        clave = normalizar(expandir(texto))
        if not clave or len(clave) < 3:
            return []
        sub = self.subconjunto(proveedor)
        mejor = {}
        for v in variantes_texto(texto):          # con y sin la cantidad de compra al inicio
            k = normalizar(expandir(v))
            if len(k) < 3:
                continue
            for h in process.extract(k, sub["clave"].tolist(), scorer=puntaje, limit=n):
                if h[1] > mejor.get(h[2], (None, -1))[1]:
                    mejor[h[2]] = (sub.iloc[h[2]], h[1])
        return sorted(mejor.values(), key=lambda x: -x[1])[:n]

    def match_alias(self, texto, proveedor="", corte=95, piso_completo=85):
        """Devuelve las filas del catálogo aprendidas para ese texto (puede ser más de una)."""
        sub = self.alias_de(proveedor)
        if not len(sub):
            return []
        # Se prueba con y sin la cantidad de compra al inicio: "15 canelones" contra el
        # alias "canelones" da 84 de token_sort y quedaba afuera por dos puntos.
        mejor = None
        for v in variantes_texto(texto):
            r = self._alias_una(v, sub, corte, piso_completo)
            if r and (mejor is None or r[0] > mejor[0]):
                mejor = r
        return mejor[1] if mejor else []

    def _alias_una(self, texto, sub, corte, piso_completo):
        """Un intento de match: devuelve (puntaje, filas) o None."""
        clave = normalizar(expandir(texto))
        # token_set da 100 cuando un alias es subconjunto de otro ("c/miel" dentro de
        # "c/miel y cacao"), así que entre los candidatos gana el que mejor cubre el texto
        cands = [(h[2], h[1]) for h in process.extract(
                 clave, sub["clave"].tolist(), scorer=fuzz.token_set_ratio, limit=len(sub))
                 if h[1] >= corte]
        if not cands:
            return None
        # token_set solo no alcanza: "BONDIOLA" es subconjunto de "BONDIOLA FET" y da 100.
        # token_sort mide el texto completo, así que un alias solo aplica si cubre casi todo.
        cands = [(i, s, fuzz.token_sort_ratio(clave, sub.iloc[i]["clave"])) for i, s in cands]
        cands = [c for c in cands if c[2] >= piso_completo]
        if not cands:
            return None
        hit = max(cands, key=lambda c: (c[2], c[1]))
        exacta = sub.iloc[hit[0]]["clave"]
        refs = [str(r).strip() for r in sub[sub["clave"] == exacta]["referencia"]]
        filas = [self.por_referencia(r) for r in dict.fromkeys(refs)]
        filas = [f for f in filas if f is not None]
        return (hit[2], filas) if filas else None

    def match_codigo(self, texto):
        for m in CODIGO.finditer(texto.upper()):
            fila = self.por_referencia(m.group(1) + m.group(2))
            if fila is not None:
                return fila
        return None

    def esta_ignorado(self, texto, *proveedores, corte=92):
        """Lo que el proveedor cotiza y el negocio no vende. Acotado al proveedor igual que
        los alias; vale tanto la marca de la fila como quien mandó el mensaje.
        Ojo con el gramaje: "yogur sin azúcar 190" no vale para el de 300."""
        ig = self.ignorar
        if not len(ig):
            return ""
        provs = [p for p in proveedores if p]
        if provs:
            ig = ig[ig["proveedor"].map(lambda p: not str(p).strip() or
                                        any(mismo_proveedor(x, p) for x in provs))]
            if not len(ig):
                return ""
        hit = process.extractOne(normalizar(expandir(texto)), ig["clave"].tolist(),
                                 scorer=fuzz.token_sort_ratio, score_cutoff=corte)
        if not hit:
            return ""
        fila = ig.iloc[hit[2]]
        if desajuste_tamano(texto, fila["texto"]):
            return ""
        return fila["motivo"] or "ignorado"


def _datos_producto(fila):
    return {"id_externo": fila.id_externo, "referencia": fila.referencia,
            "default_code": fila.default_code, "nombre_odoo": fila.nombre_completo,
            "costo_actual": float(fila.costo_actual), "pv_actual": float(fila.precio_venta),
            "publicado": bool(fila.publicado)}


def _opciones(cands):
    return [{"referencia": c.referencia, "nombre": c.nombre_completo, "codigo_odoo": c.default_code,
             "costo_actual": float(c.costo_actual), "score": round(s)} for c, s in cands]


def analizar_renglon(f, ctx, umbral_auto=UMBRAL_AUTO, umbral_dudoso=UMBRAL_DUDOSO):
    """Matchea un renglón. Devuelve una lista de registros (más de uno si un alias
    apunta a varios productos, o si un faltante abarca varios)."""
    prov, desc = f.get("proveedor", ""), f["descripcion"]

    motivo = ctx.esta_ignorado(desc, prov, f.get("proveedor_bloque", ""))
    if motivo:
        return [{**f, "estado": "ignorado", "score": 100, "via": motivo}]

    if f["tipo"] in ("faltante", "sin cambio"):
        aplican = ctx.todos_los_que_aplican(desc, prov)
        if aplican:
            return [{**f, "estado": "aviso", "score": round(sc), "via": "término general",
                     **_datos_producto(fila)} for fila, sc in aplican]

    aprendidas = ctx.match_alias(desc, prov)
    # si la marca de la fila no tiene alias para esto, probar con el del bloque
    # (un almacén mandó una picada con la marca de otro puesta por error)
    if not aprendidas and f.get("proveedor_bloque") and f["proveedor_bloque"] != prov:
        aprendidas = ctx.match_alias(desc, f["proveedor_bloque"])
        if aprendidas:
            prov = f["proveedor_bloque"]

    if len(aprendidas) > 1:
        salida = []
        for fila in aprendidas:
            reg = {**f, "estado": "ok", "score": 100, "via": "alias aprendido", **_datos_producto(fila)}
            if f["tipo"] == "costo" and fila.costo_actual:
                reg["variacion_%"] = round((f["costo_nuevo"] / fila.costo_actual - 1) * 100, 1)
                if abs(f["costo_nuevo"] - fila.costo_actual) < 0.01:
                    reg["estado"] = "sin cambio"
            salida.append(reg)
        return salida

    fila = aprendidas[0] if aprendidas else None
    via, score = ("alias aprendido", 100) if fila is not None else ("", 0)
    cands = []
    if fila is None:
        fila = ctx.match_codigo(f["linea"])
        if fila is not None:
            via, score = "código interno", 100
    if fila is None:
        cands = ctx.candidatos(desc, prov)
        # la marca de la fila es una preferencia, no una jaula: si dentro de esa
        # marca no hay nada bueno, se prueba con el proveedor que mandó el mensaje
        if (not cands or cands[0][1] < umbral_auto) and f.get("proveedor_bloque") \
           and f["proveedor_bloque"] != prov:
            otros = ctx.candidatos(desc, f["proveedor_bloque"])
            if otros and (not cands or otros[0][1] > cands[0][1]):
                cands, prov = otros, f["proveedor_bloque"]
        if cands:
            fila, score = cands[0]
            via = f"nombre ({prov})" if prov else "nombre (sin proveedor)"
            # variantes con el mismo nombre (talles, colores): el nombre no alcanza para
            # saber cuál es, así que nunca entra sola
            if len(cands) > 1 and cands[1][0].clave == fila.clave:
                score = min(score, umbral_auto - 1)
            f["alternativas"] = " | ".join(f"{c.referencia}={c.nombre_completo} ({s:.0f})"
                                           for c, s in cands[1:])

    reg = dict(f)
    reg["opciones"] = _opciones(cands)
    if fila is None or score < umbral_dudoso:
        reg.update({"estado": "sin match", "score": round(score), "via": via})
        return [reg]

    reg.update({"estado": "ok" if score >= umbral_auto else "revisar",
                "score": round(score), "via": via, **_datos_producto(fila),
                "desajuste": desajuste_tamano(desc, fila.nombre_completo)})
    if f["tipo"] == "costo":
        # quien mandó el mensaje va último: es el que dice si el precio trae IVA ("La Quesera"
        # aunque en Odoo figure como "Lácteos Don Julio")
        remitente = f.get("proveedor_bloque") or ""
        transform.aplicar(reg, f["costo_nuevo"], [prov] + ([remitente] if remitente and remitente != prov else []),
                          fila, ctx.impuestos, ctx.reglas, ctx.costo_sin_iva)
        if fila.costo_actual and not reg.get("falta_iva"):
            nuevo = reg["costo_nuevo"]
            reg["variacion_%"] = round((nuevo / fila.costo_actual - 1) * 100, 1)
            if abs(nuevo - fila.costo_actual) < 0.01:
                reg["estado"] = "sin cambio"
    return [reg]


def analizar(renglones, ctx):
    salida = []
    for f in renglones:
        salida.extend(analizar_renglon(dict(f), ctx))
    return pd.DataFrame(salida)


# ---------- listas de distribuidoras ----------
UMBRAL_LISTA = 70   # debajo de esto, el artículo de la lista no es algo que se venda


def marca_en_texto(ctx, texto):
    """Primera marca del catálogo cuyas palabras están todas en el texto."""
    toks = set(normalizar(expandir(texto)).split())
    mejor = None
    for palabras, prov in ctx._marcas:
        if palabras <= toks and (mejor is None or len(palabras) > len(mejor[0])):
            mejor = (palabras, prov)
    return mejor[1] if mejor else ""


# Los candidatos por nombre de una lista larga son lo más caro de calcular y no cambian
# con las decisiones: se guardan mientras el catálogo sea el mismo.
_CACHE_CANDIDATOS = {}


def _candidatos_cacheados(ctx, desc, marcas):
    """Candidatos dentro de una o varias marcas (tupla)."""
    clave = (id(ctx.catalogo), len(ctx.catalogo), marcas, desc)
    if clave not in _CACHE_CANDIDATOS:
        todos = [c for m in marcas for c in ctx.candidatos(desc, m)]
        vistos, unicos = set(), []
        for fila, s in sorted(todos, key=lambda x: -x[1]):
            if fila.referencia not in vistos:
                vistos.add(fila.referencia)
                unicos.append((fila, s))
        _CACHE_CANDIDATOS[clave] = unicos[:3]
    return _CACHE_CANDIDATOS[clave]


def marcas_del_distribuidor(ctx, distribuidor, codigos):
    """Marcas que ya se le compran a esta distribuidora: las de los productos con código
    aprendido y las de los productos cuya fuente habitual es ella."""
    refs = {r for r in codigos.values() if r}
    refs |= {r for r, f in ctx.memoria.fuentes_habituales().items() if mismo_proveedor(f, distribuidor)}
    marcas = set()
    for r in refs:
        fila = ctx.por_referencia(r)
        if fila is not None and fila.proveedor:
            marcas.add(fila.proveedor)
    return tuple(sorted(marcas))


def analizar_articulo(a, ctx, distribuidor, codigos, marcas_conocidas=(), unidades=None):
    """Un renglón de una lista de distribuidora. La lista trae de todo; solo interesa lo
    que se vende. Orden: código ya aprendido -> alias -> nombre (siempre a confirmar la
    primera vez; al confirmar se aprende el código y el mes siguiente entra solo).

    La marca sale de: la columna de marca, la descripción ("ATUN BAHIA"), el título de la
    sección ("BAHIA" arriba de todo), y si no, las marcas que ya se le compran."""
    unidades = unidades or {}
    marca = a.get("marca") or marca_en_texto(ctx, a["descripcion"]) or a.get("marca_seccion", "")
    base = {"proveedor": marca or distribuidor, "proveedor_bloque": distribuidor,
            "descripcion": a["descripcion"], "costo_nuevo": a["precio"], "precio_sugerido": None,
            "linea": a["linea"], "tipo": "costo", "codigo": a.get("codigo", ""), "es_lista": True,
            "fuente": a.get("fuente", distribuidor), "origen": a.get("origen", ""),
            "huella": a.get("huella", "")}

    cod = clave_codigo(base["codigo"]) if base["codigo"] else ""
    fila, via, score, cands = None, "", 0, []
    if a.get("sin_stock"):
        # sin stock: solo importa si es algo que se le compra (código ya aprendido)
        fila = ctx.por_referencia(codigos.get(cod, "")) if cod else None
        if fila is None:
            return []
        return [{**base, "tipo": "faltante", "estado": "aviso", "score": 100, "mes": "",
                 "via": "la lista dice sin stock", **_datos_producto(fila)}]
    if cod and cod in codigos:
        ref = codigos[cod]
        if not ref:
            return [{**base, "estado": "ignorado", "score": 100, "via": "no lo vendemos (código de la lista)"}]
        fila = ctx.por_referencia(ref)
        if fila is None:
            return [{**base, "estado": "sin match", "score": 0, "opciones": [],
                     "via": f"el código estaba asociado a {ref}, que ya no está en Odoo"}]
        via, score = "código de la lista", 100
    else:
        motivo = ctx.esta_ignorado(base["descripcion"], marca, distribuidor)
        if motivo:
            return [{**base, "estado": "ignorado", "score": 100, "via": motivo}]
        aprendidas = ctx.match_alias(base["descripcion"], marca) if marca else []
        if not aprendidas:
            aprendidas = ctx.match_alias(base["descripcion"], distribuidor)
        if len(aprendidas) == 1:
            fila, via, score = aprendidas[0], "alias aprendido", 100
        else:
            # sin marca detectada y sin historial con esta distribuidora (proveedor nuevo,
            # o su primera lista): buscar en todo el catálogo en vez de en nada
            marcas = (marca,) if marca else tuple(marcas_conocidas) or ("",)
            cands = _candidatos_cacheados(ctx, base["descripcion"], marcas)
            if not cands or cands[0][1] < UMBRAL_LISTA:
                return [{**base, "estado": "fuera de catálogo", "score": round(cands[0][1]) if cands else 0,
                         "via": "no se parece a nada del catálogo"}]
            fila, score = cands[0]
            via = f"nombre ({marca or fila.proveedor})"

    reg = {**base, "opciones": _opciones(cands)}
    # por nombre, una lista nunca entra sola: la primera vez se confirma y se aprende el código
    estado = "ok" if via in ("código de la lista", "alias aprendido") else "revisar"
    reg.update({"estado": estado, "score": round(score), "via": via, **_datos_producto(fila),
                "desajuste": desajuste_tamano(base["descripcion"], fila.nombre_completo)})
    precio = a["precio"]
    n = unidades.get(cod) if cod else None
    if n:                      # ya se sabe que este código se cotiza por bulto
        precio = round(precio / n, 4)
        reg.update({"costo_nuevo": precio, "precio_bulto": a["precio"], "unidades_bulto": n, "desajuste": "",
                    "via": f"{reg['via']} · precio del bulto de {n}"})
    transform.aplicar(reg, precio, [marca, distribuidor], fila, ctx.impuestos, ctx.reglas, ctx.costo_sin_iva)
    if fila.costo_actual and not reg.get("falta_iva"):
        reg["variacion_%"] = round((reg["costo_nuevo"] / fila.costo_actual - 1) * 100, 1)
        if abs(reg["costo_nuevo"] - fila.costo_actual) < 0.01:
            reg["estado"] = "sin cambio"
        if not n:
            _sugerir_bulto(reg, base["descripcion"], fila.costo_actual, reg["costo_nuevo"])
    elif fila.costo_actual and not n:
        # todavía falta el IVA: se estima con el sugerido, así la tarjeta del IVA ya avisa del bulto
        estimado = transform.costo_de_lista(precio, reg["iva_sugerido"], reg.get("percepcion", 0),
                                            reg["iva_incluido"], ctx.costo_sin_iva)
        _sugerir_bulto(reg, base["descripcion"], fila.costo_actual, estimado)
    return [reg]


BULTO_N = re.compile(r"\b(\d{1,3})\s*[xX]\s*\d")


def _sugerir_bulto(reg, descripcion, costo_actual, costo):
    """"HARINA 000 10 x 1 Kg $7.810" contra $781 en Odoo: la lista cotiza el bulto de 10.
    Si dividir por la cantidad del bulto deja el costo cerca del actual y sin dividir
    queda lejísimos, se sugiere (la persona confirma)."""
    m = BULTO_N.search(descripcion)
    if not m or int(m.group(1)) < 2:
        return
    n = int(m.group(1))
    entero = costo / costo_actual - 1
    por_unidad = costo / n / costo_actual - 1
    if abs(entero) > 0.5 and abs(por_unidad) < 0.35:
        reg["sugerir_bulto"] = n
        reg["costo_por_unidad"] = round(costo / n, 2)


def analizar_lista(articulos, ctx, distribuidor):
    codigos = ctx.memoria.codigos_de(distribuidor)
    unidades = ctx.memoria.unidades_de(distribuidor)
    conocidas = marcas_del_distribuidor(ctx, distribuidor, codigos)
    salida = []
    for a in articulos:
        salida.extend(analizar_articulo(a, ctx, distribuidor, codigos, conocidas, unidades))
    return salida
