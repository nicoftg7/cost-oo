"""Reglas impositivas del país (Argentina). Todo lo que dependa de la ley de IVA vive acá,
así adaptar la app a otro país es cambiar este archivo y nada más."""
import re

from .normalize import sin_acentos

PAIS = "Argentina"

# Qué hay que sumarle a la lista de un proveedor para llegar al costo.
IVA_OPCIONES = {
    "incluido": "No sumar nada: es el precio final",
    "21": "Sin IVA: sumar 21%",
    "21+3": "Sin IVA: sumar 21% + 3% de percepción",
    "mixto": "Sin IVA: 21% o 10,5% según el producto",
    "mixto+3": "Sin IVA: 21% o 10,5% según el producto, + 3% de percepción",
}
IVA_GENERAL = 0.21
IVA_REDUCIDO = 0.105
PERCEPCION = 0.03

# en impuestos.csv, un proveedor "mixto" guarda esto como IVA: cada producto tiene el suyo
IVA_POR_PRODUCTO = "según producto"


def tasas_de_opcion(valor):
    """'21+3' -> (0.21, 0.03); 'mixto' -> (IVA_POR_PRODUCTO, 0.0); 'incluido' -> None."""
    if valor == "incluido" or valor not in IVA_OPCIONES:
        return None
    iva = IVA_POR_PRODUCTO if valor.startswith("mixto") else IVA_GENERAL
    return iva, PERCEPCION if valor.endswith("+3") else 0.0


# Alícuota reducida (10,5%): harina, pan, carnes, frutas y verduras frescas, etc.
# Es solo una sugerencia: la persona confirma el IVA de cada producto una vez.
REDUCIDO = re.compile(r"\b(harinas?|pan(es)?|galletit\w*|facturas?|carnes?|frutas?|verduras?|hortalizas?|"
                      r"legumbres? (frescas?|secas?)|miel a granel)\b", re.I)


def sugerir_iva(nombre):
    return IVA_REDUCIDO if REDUCIDO.search(sin_acentos(str(nombre))) else IVA_GENERAL
