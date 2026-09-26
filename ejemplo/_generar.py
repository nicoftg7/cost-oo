"""Genera los archivos del negocio de ejemplo (todo inventado).

Un almacén ficticio con proveedores ficticios, pensado para mostrar cada caso que la app
sabe resolver. Los precios de los mensajes y las listas son los costos del catálogo
+10%, como si fuera el ciclo siguiente.

Solo hace falta correrlo si se cambia el ejemplo:  python ejemplo/_generar.py
(necesita reportlab para el PDF, que no es requisito de la app).
"""
import csv
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent
SUBE = 1.10

# (referencia, nombre en Odoo, costo, precio de venta)
CATALOGO = [
    ("PAN001", "Pan de campo 800 g - Panadería La Espiga", 2500, 3750),
    ("PAN002", "Pan integral 600 g - Panadería La Espiga", 2300, 3450),
    ("PAN003", "Prepizza napolitana x 2 - Panadería La Espiga", 2000, 3000),
    ("PAN004", "Budín de limón 350 g - Panadería La Espiga", 3200, 4800),
    ("PAN005", "Budín de chocolate 350 g - Panadería La Espiga", 3400, 5100),
    ("PAS001", "Fideos al huevo 500 g - Pastas Río", 2800, 4200),
    ("PAS002", "Ravioles de ricota 1 kg - Pastas Río", 6000, 9000),
    ("PAS003", "Ñoquis de papa 750 g - Pastas Río", 3000, 4500),
    ("PAS004", "Canelones de verdura x 6 - Pastas Río", 8000, 12000),
    ("LAC001", "Queso cremoso 500 g - Lácteos Don Julio", 6400, 9600),
    ("LAC002", "Queso reggianito 250 g - Lácteos Don Julio", 4700, 7050),
    ("LAC003", "Dulce de leche 400 g - Lácteos Don Julio", 3200, 4800),
    ("LAC004", "Yogur natural 1 l - Lácteos Don Julio", 2900, 4350),
    ("LAC005", "Queso azul 200 g - Lácteos Don Julio", 3900, 5850),
    ("ESP001", "Orégano 25 g - Especias Luna", 600, 900),
    ("ESP002", "Pimentón 25 g - Especias Luna", 610, 915),
    ("ESP003", "Comino 25 g - Especias Luna", 640, 960),
    ("ESP004", "Canela molida 25 g - Especias Luna", 700, 1050),
    ("MIE001", "Miel 500 g - Miel del Monte", 4750, 7125),
    ("MIE002", "Miel 1 kg - Miel del Monte", 7300, 10950),
    ("MIE003", "Miel (envase plástico) 250 g - Miel del Monte", 3000, 4500),
    ("EMB001", "Bondiola feteada 150 g - Embutidos La Sierra", 3200, 4800),
    ("EMB002", "Salame picado grueso 300 g - Embutidos La Sierra", 5900, 8850),
    ("EMB003", "Jamón crudo feteado 100 g - Embutidos La Sierra", 2300, 3450),
    ("LIM001", "Lavandina 1 l - Limpieza Clara", 850, 1275),
    ("LIM002", "Detergente 750 cc - Limpieza Clara", 1400, 2100),
    ("CON001", "Atún lomito al natural 170 g - Conservas Marina", 2100, 3150),
    ("CON002", "Atún desmenuzado 170 g - Conservas Marina", 1000, 1500),
    ("CON003", "Palmitos en trozos 400 g - Conservas Marina", 1450, 2175),
    ("CON004", "Arvejas remojadas 300 g - Conservas Marina", 480, 720),
    ("HAR001", "Harina 000 1 kg - Molino Pampa", 800, 1200),
    ("FID001", "Fideos tallarín secos 500 g - Molino Pampa", 750, 1125),
    ("YER001", "Yerba mate 1 kg - Yerbal del Litoral", 2700, 4050),
    ("ACE001", "Aceite de girasol 900 cc - Óleos del Plata", 2600, 3900),
]
NO_PRODUCTOS = [("", "10% en tu orden", 0, 0), ("", "Cuota social mensual", 0, 0)]


def p(ref):
    """Precio nuevo del ciclo de ejemplo (+10%), redondeado como lo escribiría un proveedor."""
    return round(next(c for r, _, c, _ in CATALOGO if r == ref) * SUBE)


def export():
    filas = [{"id": f"__export__.product_template_{1000 + i}_ej{i:04x}", "name": n, "default_code": r,
              "standard_price": f"{c:.2f}", "list_price": f"{pv:.2f}", "is_published": "True",
              "qty_available": "0.0"} for i, (r, n, c, pv) in enumerate(CATALOGO + NO_PRODUCTOS)]
    pd.DataFrame(filas).to_csv(AQUI / "export_odoo.csv", index=False, quoting=csv.QUOTE_ALL)


def planilla():
    mensajes = [
        ("Panadería La Espiga", f"""Hola! Te paso los precios de octubre 🙂
🍞 Pan de campo ${p('PAN001')}
Pan integral ${p('PAN002')}
Prepizzas x2 ${p('PAN003')}
Budines ${p('PAN004')} ({round(p('PAN004') * 1.5)})
    Limón
    Chocolate"""),
        ("Pastas Río", f"""Fideos al huevo 500g ${p('PAS001')}
Ravioles ricota x kg ${p('PAS002')}
Ñoquis x 750 grs ${p('PAS003')}
Canelones igual"""),
        # un apodo: en Odoo el proveedor se llama "Lácteos Don Julio"
        ("La Quesera", f"""Cremoso x 500 ${p('LAC001')}
Reggianito x 250g ${p('LAC002')}
Dulce de leche ${p('LAC003')}
Yogur natural 1 litro ${p('LAC004')}
Sin quesos azules"""),
        # precio en la línea de abajo, y la canela cotizada en otra presentación
        ("Especias Luna", f"""Orégano x 25 g ${p('ESP001')}
Pimentón x 25g ${p('ESP002')}
Comino x 25 g.
$ {p('ESP003')}
Canela x 50g ${p('ESP004') * 2}"""),
        # títulos sin precio que valen para los renglones de abajo, y un teléfono
        ("Miel del Monte", f"""Miel en frascos de vidrio.
360 cm3 (1/2 kilo) ${p('MIE001')}
650 cm3 (1 kg) ${p('MIE002')}
Miel en envases de plástico.
x200cm3 ${p('MIE003')}
Pedidos: 3415550000"""),
        # cotiza por kilo (regla en la memoria del ejemplo)
        ("Embutidos La Sierra", f"""LISTA - precios por kilo
BONDIOLA FET ${round(p('EMB001') / 0.15)}
SALAME PICADO GRUESO ${round(p('EMB002') / 0.3)}
JAMON CRUDO FET ${round(p('EMB003') / 0.1)}"""),
        # líneas de factura: el costo es Importe / Cantidad (ya con el descuento)
        ("Limpieza Clara", f"""01  1001 CLARA. LAVANDINA 1 L          24   {p('LIM001'):.2f}   0.00  {p('LIM001') * 24:.2f}
02  1002 CLARA. DETERGENTE 750 CC      12   {p('LIM002') / 0.9:.2f}  10.00  {p('LIM002') * 12:.2f}"""),
        ("Verdulería Don Tito", ""),
    ]
    pd.DataFrame(mensajes, columns=["Proveedor", "Mensaje"]).to_csv(AQUI / "planilla_mensajes.csv", index=False)


def lista_excel():
    """Distribuidora Sur: sin IVA, con harina al 10,5% y el resto al 21%, y el aceite por bulto."""
    def neto(ref, iva):
        return round(p(ref) / (1 + iva), 2)
    filas = [
        ("30011", "HARINA 000 MOLINO PAMPA 1 KG", "MOLINO PAMPA", neto("HAR001", 0.105)),
        ("30012", "FIDEOS TALLARIN MOLINO PAMPA 500 G", "MOLINO PAMPA", neto("FID001", 0.21)),
        ("30020", "YERBA MATE YERBAL DEL LITORAL 1 KG", "YERBAL DEL LITORAL", neto("YER001", 0.21)),
        ("30031", "ACEITE GIRASOL OLEOS DEL PLATA 12 x 900 CC", "OLEOS DEL PLATA", round(neto("ACE001", 0.21) * 12, 2)),
        ("30040", "AZUCAR REFINADA DULCEMAR 1 KG", "DULCEMAR", 980.00),
        ("30041", "ARROZ LARGO FINO GRANO DE ORO 1 KG", "GRANO DE ORO", 1250.00),
        ("30050", "SAL FINA SALINAS DEL SUR 500 G", "SALINAS DEL SUR", 540.00),
        ("30051", "VINAGRE DE ALCOHOL CLARITO 1 L", "CLARITO", 870.00),
    ]
    df = pd.DataFrame(filas, columns=["Código", "Descripción", "Marca", "Precio s/IVA"])
    df.to_excel(AQUI / "lista Distribuidora Sur.xlsx", index=False)


def lista_pdf():
    """Distribuidora Norte: código de barras, código, lista, descuento y neto; la marca solo en el título."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    def renglon(ean, cod, desc, pres, ref=None, precio=None, dto2=None, sin_stock=False):
        neto = 0.0 if sin_stock else (p(ref) / 1.21 if ref else precio)
        lista = 0.0 if sin_stock else neto / (1 - 0.1878) / (1 - (dto2 or 0) / 100)
        d2 = f" {dto2:.2f}%" if dto2 else ""
        return f"{ean} {cod} {desc}{' SIN STOCK' if sin_stock else ''} 88 {pres} {lista:,.2f} 18.78%{d2} {neto:,.2f}"

    lineas = ["DISTRIBUIDORA NORTE S.A.", "CONSERVAS MARINA",
              "CONSERVAS DE PESCADO BP PRESENT. LISTA DCTO NETO",
              renglon("7790000000011", "01-0112", "Atún Lomitos al Natural (Ecuador)", "48 x 170 grs.", "CON001"),
              renglon("7790000000028", "01-0111", "Atún Lomitos en Aceite (Ecuador)", "48 x 170 grs.", precio=1909.10),
              renglon("7790000000035", "01-2013", "Atún Desmenuzado al natural (Ecuador)", "48 x 170 grs.", "CON002", dto2=5),
              renglon("7790000000042", "01-0127", "Sardinas en aceite (Tailandia)", "50 x 125 grs.", precio=1320.50),
              renglon("7790000000059", "01-0172", "Caballa al Natural (Nacional)", "24 x 380 gr.", sin_stock=True),
              "CONSERVAS DE VEGETALES",
              renglon("7790000000066", "02-0146", "Palmitos en Trozos (Ecuador)", "24 x 400 grs.", "CON003"),
              renglon("7790000000073", "02-1002", "Arvejas Secas Remojadas", "24 x 300 grs.", "CON004"),
              renglon("7790000000080", "02-1007", "Grano de Choclo Amarillo", "24 x 300 grs.", precio=601.50),
              "PRECIOS MAS IVA", "LISTA - OCTUBRE (EJEMPLO, DATOS INVENTADOS)"]
    c = canvas.Canvas(str(AQUI / "lista Distribuidora Norte.pdf"), pagesize=A4)
    y = 800
    for l in lineas:
        c.setFont("Helvetica", 8)
        c.drawString(30, y, l)
        y -= 14
    c.save()


def memoria():
    """Lo mínimo que un negocio carga al empezar (en la app se hace desde la bienvenida)."""
    m = AQUI / "memoria"
    m.mkdir(exist_ok=True)
    tablas = {
        "proveedores": (["nombre", "alias", "tipo", "iva_lista", "margen", "nota"], [
            ("Panadería La Espiga", "", "mensaje", "", "fijo", ""),
            ("Pastas Río", "", "mensaje", "", "fijo", ""),
            ("La Quesera", "", "mensaje", "", "fijo", "en Odoo figura como Lácteos Don Julio"),
            ("Especias Luna", "", "mensaje", "", "fijo", ""),
            ("Miel del Monte", "", "mensaje", "", "fijo", ""),
            ("Embutidos La Sierra", "", "mensaje", "", "fijo", ""),
            ("Limpieza Clara", "", "mensaje", "", "fijo", ""),
            ("Verdulería Don Tito", "", "manual", "", "fijo", ""),
            ("Distribuidora Norte", "DN", "lista", "21", "fijo", ""),
            ("Distribuidora Sur", "", "lista", "mixto", "fijo", "harina al 10,5%, el resto al 21%"),
        ]),
        "proveedor_alias": (["alias", "proveedor"], [("La Quesera", "Lácteos Don Julio")]),
        "reglas_proveedor": (["proveedor", "cotiza_por", "iva", "nota"],
                             [("Embutidos La Sierra", "kilo", "incluido", "manda el precio por kilo")]),
        "impuestos": (["ambito", "clave", "iva", "percepcion", "nota"], [
            ("proveedor", "Distribuidora Norte", "0.21", "0", "la lista dice PRECIOS MAS IVA"),
            ("proveedor", "Distribuidora Sur", "según producto", "0", "harina al 10,5%, el resto al 21%")]),
        "no_son_productos": (["empieza_con", "nota"], [("cuota social", "no es un producto")]),
    }
    for nombre, (cols, filas) in tablas.items():
        pd.DataFrame(filas, columns=cols).to_csv(m / f"{nombre}.csv", index=False)


if __name__ == "__main__":
    export()
    planilla()
    lista_excel()
    lista_pdf()
    memoria()
    print("ejemplo generado en", AQUI)
