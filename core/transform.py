"""Transformaciones de precio: lo que cotiza el proveedor no siempre es lo que se
guarda como costo en Odoo."""
from rapidfuzz import fuzz

from .normalize import normalizar, peso_kg
from .pais import sugerir_iva  # noqa: F401  (las reglas del país viven en pais.py)


POR_PRODUCTO = "por producto"


def impuesto_de(proveedor, referencia, imp):
    """(iva, percepcion) para ese producto, o None. La regla por referencia pisa a la
    del proveedor. Si el proveedor es "mixto" y el producto todavía no tiene la suya,
    devuelve (POR_PRODUCTO, percepcion): hay que preguntar."""
    if not len(imp):
        return None
    porref = imp[(imp.ambito == "referencia") & (imp.clave.astype(str) == str(referencia))]
    if len(porref):
        r = porref.iloc[0]
        return float(r.iva), float(r.percepcion)
    obj = normalizar(proveedor or "")
    for r in imp[imp.ambito == "proveedor"].itertuples():
        if obj and fuzz.token_set_ratio(obj, normalizar(r.clave)) >= 88:
            if getattr(r, "por_producto", False):
                return POR_PRODUCTO, float(r.percepcion)
            return float(r.iva), float(r.percepcion)
    return None




def regla_de(proveedor, reglas):
    if not len(reglas) or not proveedor:
        return {}
    obj = normalizar(proveedor)
    for r in reglas.itertuples():
        if fuzz.token_set_ratio(obj, normalizar(r.proveedor)) >= 88:
            return {"cotiza_por": r.cotiza_por, "iva": r.iva}
    return {}


def con_impuestos(neto, iva, percepcion):
    """Lista sin impuestos: precio × (1 + IVA) × (1 + percepción)."""
    return round(neto * (1 + iva) * (1 + percepcion), 2)


def por_kilo(precio_kilo, nombre_odoo):
    """Regla de tres con el peso que sale del nombre del producto en Odoo.
    Devuelve (costo, kg) o (None, None) si el nombre no dice el peso."""
    kg = peso_kg(nombre_odoo)
    if not kg:
        return None, None
    return round(precio_kilo * kg, 2), kg


def aplicar(reg, costo_mensaje, proveedor, fila, impuestos, reglas):
    """Aplica impuestos y regla por kilo sobre un renglón ya matcheado (lo modifica).

    proveedor: un nombre, o varios en orden de preferencia (marca, distribuidora): la
    primera regla que aparezca es la que vale."""
    provs = [p for p in ([proveedor] if isinstance(proveedor, str) else proveedor) if p]
    via = reg["via"]
    tas = next((t for t in (impuesto_de(p, fila.referencia, impuestos) for p in provs or [""]) if t), None)
    if tas and tas[0] == POR_PRODUCTO:
        # sin saber el IVA de este producto el costo no se puede calcular: se pregunta una vez
        reg.update({"falta_iva": True, "percepcion": tas[1], "precio_lista": costo_mensaje,
                    "iva_sugerido": sugerir_iva(fila.nombre_completo),
                    "via": f"{via} · falta saber si lleva IVA 21% o 10,5%"})
        return reg
    if tas:
        iva, perc = tas
        reg["precio_lista"] = costo_mensaje
        reg["costo_nuevo"] = con_impuestos(costo_mensaje, iva, perc)
        reg["via"] = f"{via} · +IVA {iva*100:g}% +percep {perc*100:g}%"

    r = next((x for x in (regla_de(p, reglas) for p in provs) if x), {})
    if r.get("cotiza_por") == "kilo":
        costo, kg = por_kilo(costo_mensaje, fila.nombre_completo)
        if costo is not None:
            reg["precio_por_kilo"] = costo_mensaje
            reg["costo_nuevo"] = costo
            reg["via"] = f"{via} · regla de tres ({kg:g} kg)"
        else:
            reg["estado"] = "revisar"
            reg["via"] = f"{via} · cotiza por kilo pero el nombre no dice el peso"
    return reg
