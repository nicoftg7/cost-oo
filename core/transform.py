"""Transformaciones de precio: lo que cotiza el proveedor no siempre es lo que se
guarda como costo en Odoo."""
from rapidfuzz import fuzz

from .normalize import normalizar, peso_kg
from .pais import IVA_GENERAL, sugerir_iva  # las reglas del país viven en pais.py


POR_PRODUCTO = "por producto"


def impuesto_de(proveedores, referencia, imp):
    """(iva, percepcion, incluido) para ese producto, o None. incluido dice si la lista
    ya trae el IVA, y sale siempre del proveedor. La alícuota por referencia pisa a la del
    proveedor. Si el proveedor es "mixto" y el producto todavía no tiene la suya, devuelve
    (POR_PRODUCTO, percepcion, incluido): hay que preguntar.

    proveedores: uno o varios en orden de preferencia (marca, distribuidora)."""
    if not len(imp):
        return None
    reglas = list(imp[imp.ambito == "proveedor"].itertuples())
    objs = [normalizar(p) for p in ([proveedores] if isinstance(proveedores, str) else proveedores) if p]
    # mismo criterio que la ficha de proveedores: con token_set, "Distribuidora Dos" se
    # quedaba con el IVA de "Distribuidora Uno" (88 puntos). Los otros nombres del proveedor
    # ya vienen como reglas propias (Memoria.impuestos)
    prov = next((r for o in objs for r in reglas
                 if o == normalizar(r.clave) or fuzz.token_sort_ratio(o, normalizar(r.clave)) >= 93), None)
    if prov is None:
        return None
    incluido = bool(getattr(prov, "incluido", False))
    porref = imp[(imp.ambito == "referencia") & (imp.clave.astype(str) == str(referencia))]
    if len(porref):
        r = porref.iloc[0]
        return float(r.iva), float(r.percepcion), incluido
    if getattr(prov, "por_producto", False):
        return POR_PRODUCTO, float(prov.percepcion), incluido
    return float(prov.iva), float(prov.percepcion), incluido




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


def costo_de_lista(precio, iva, percepcion, incluido, costo_sin_iva):
    """Lo que va como costo en Odoo según cómo viene la lista y cómo carga el negocio.
    Con el costo sin IVA la percepción tampoco va: se descuenta como el IVA."""
    if costo_sin_iva:
        return round(precio / (1 + iva), 2) if incluido else precio
    return precio if incluido else con_impuestos(precio, iva, percepcion)


def hace_falta_alicuota(incluido, costo_sin_iva):
    """Sumar o sacar el IVA necesita saber si es 21% o 10,5%; dejar el precio como está, no."""
    return incluido == costo_sin_iva


def por_kilo(precio_kilo, nombre_odoo):
    """Regla de tres con el peso que sale del nombre del producto en Odoo.
    Devuelve (costo, kg) o (None, None) si el nombre no dice el peso."""
    kg = peso_kg(nombre_odoo)
    if not kg:
        return None, None
    return round(precio_kilo * kg, 2), kg


def aplicar(reg, costo_mensaje, proveedor, fila, impuestos, reglas, costo_sin_iva=False):
    """Aplica impuestos y regla por kilo sobre un renglón ya matcheado (lo modifica).

    proveedor: un nombre, o varios (marca, distribuidora). Para cotizar por kilo vale la
    primera regla que aparezca; para el IVA, solo la de la distribuidora. costo_sin_iva: el negocio carga el costo
    sin IVA, así que a una lista con IVA se le saca en vez de sumárselo a una sin IVA."""
    provs = [p for p in ([proveedor] if isinstance(proveedor, str) else proveedor) if p]
    via = reg["via"]
    # el IVA es de la lista o del mensaje: lo dice quien manda el precio (el último: la
    # distribuidora, no la marca), y solo él. Si no se sabe, se pregunta
    tas = impuesto_de(provs[-1:], fila.referencia, impuestos)
    if tas is None and provs:
        # no se sabe si este proveedor cotiza con o sin IVA: el costo no entra hasta
        # saberlo. Se pregunta una vez por proveedor (el que manda el precio: el último)
        reg.update({"falta_iva_proveedor": True, "proveedor_iva": provs[-1], "precio_lista": costo_mensaje,
                    "via": f"{via} · falta saber si {provs[-1]} cotiza con o sin IVA"})
    if tas and tas[0] == POR_PRODUCTO and hace_falta_alicuota(tas[2], costo_sin_iva):
        # sin saber el IVA de este producto el costo no se puede calcular: se pregunta una vez
        reg.update({"falta_iva": True, "percepcion": 0.0 if costo_sin_iva else tas[1],
                    "precio_lista": costo_mensaje, "iva_incluido": tas[2],
                    "iva_sugerido": sugerir_iva(fila.nombre_completo),
                    "via": f"{via} · falta saber si lleva IVA 21% o 10,5%"})
        return reg
    if tas:
        iva, perc, incluido = tas
        iva = IVA_GENERAL if iva == POR_PRODUCTO else iva     # no se usa: el precio queda igual
        reg["precio_lista"] = costo_mensaje
        reg["costo_nuevo"] = costo_de_lista(costo_mensaje, iva, perc, incluido, costo_sin_iva)
        if costo_sin_iva and incluido:
            reg["via"] = f"{via} · sin el IVA {iva*100:g}% de la lista"
        elif costo_sin_iva:
            reg["via"] = f"{via} · lista sin IVA, costo sin IVA"
        elif not incluido:
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
