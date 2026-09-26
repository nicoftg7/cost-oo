"""Reglas impositivas del país (Argentina). Todo lo que dependa de la ley de IVA vive acá,
así adaptar la app a otro país es cambiar este archivo y nada más."""
import re

from .normalize import sin_acentos

PAIS = "Argentina"

# Cómo vienen los precios de la lista de un proveedor. Con eso y con cómo carga el negocio el
# costo en Odoo (con IVA o sin IVA) se sabe si hay que sumar el IVA, sacarlo o dejarlo igual.
IVA_OPCIONES = {
    "incluido": "Con IVA incluido (21%)",
    "incluido-mixto": "Con IVA incluido: 21% o 10,5% según el producto",
    "21": "Sin IVA (21%)",
    "21+3": "Sin IVA (21%) + 3% de percepción",
    "mixto": "Sin IVA: 21% o 10,5% según el producto",
    "mixto+3": "Sin IVA: 21% o 10,5% según el producto, + 3% de percepción",
}
IVA_GENERAL = 0.21
IVA_REDUCIDO = 0.105
PERCEPCION = 0.03

# en impuestos.csv, un proveedor "mixto" guarda esto como IVA: cada producto tiene el suyo
IVA_POR_PRODUCTO = "según producto"


def tasas_de_opcion(valor):
    """'21+3' -> (0.21, 0.03, False); 'mixto' -> (IVA_POR_PRODUCTO, 0.0, False);
    'incluido' -> (0.21, 0.0, True). El último valor dice si el precio ya trae el IVA."""
    if valor not in IVA_OPCIONES:
        return None
    iva = IVA_POR_PRODUCTO if "mixto" in valor else IVA_GENERAL
    return iva, PERCEPCION if valor.endswith("+3") else 0.0, valor.startswith("incluido")


# Alícuota reducida (10,5%): harina, pan, carnes, frutas y verduras frescas, etc.
# Es solo una sugerencia: la persona confirma el IVA de cada producto una vez.
REDUCIDO = re.compile(r"\b(harinas?|pan(es)?|galletit\w*|facturas?|carnes?|frutas?|verduras?|hortalizas?|"
                      r"legumbres? (frescas?|secas?)|miel a granel)\b", re.I)


def sugerir_iva(nombre):
    return IVA_REDUCIDO if REDUCIDO.search(sin_acentos(str(nombre))) else IVA_GENERAL
