"""Fuentes nuevas: listas con código, fuente habitual, mensajes ya procesados,
Google Sheets y la verificación después de importar."""
import io
import urllib.error

import pandas as pd
import pytest

from core import ErrorDeDatos, sheets
from core.catalog import construir
from core.cycle import correr_fuentes
from core.output import control
from core.pricelist import _columnas, renglones_de_texto
from core.verify import verificar

from conftest import PRODUCTOS, export_df


def planilla(*filas):
    buf = io.StringIO()
    pd.DataFrame(filas, columns=["Proveedor", "Chequeado", "Mensaje"]).to_csv(buf, index=False)
    return {"tipo": "planilla", "nombre": "planilla", "contenido": buf.getvalue().encode("utf-8")}


def lista(distribuidor, *lineas, fecha=None):
    from core.providers import indice_proveedores
    articulos, _ = renglones_de_texto(list(lineas), indice_proveedores(construir(export_df())))
    return {"tipo": "lista", "nombre": "lista.pdf", "proveedor": distribuidor,
            "articulos": articulos, "no_leidos": [], "huella": "h-" + distribuidor, "fecha": fecha}


# ---------- lectura de listas ----------
def test_lista_con_pesos_y_numero_partido():
    filas, _ = renglones_de_texto([
        "80018 Aceite ZANONI Girasol x 900cc (12 UxB) ZANONI $ 2.598 0",
        "81671 Aceite ZANONI Girasol x 5000cm3 Bidon ZANONI $ 1 4.294 0",
    ])
    assert [f["precio"] for f in filas] == [2598.0, 14294.0]
    assert "UxB" not in filas[0]["descripcion"]


def test_lista_con_punto_decimal():
    filas, _ = renglones_de_texto([
        "AL-0014 ATUN BAHIA LOMITO NATUR 170GR PR 2670.631 48",
        "AL-0286 GARRAP. MANI 80G GUADALEST 452.111 150 NUEVO",
        "CP-0016 APOSI DISMAR CURITAS 48U C/U 371.003 30X48 NUEVO",
    ])
    # "452.111" solo es decimal porque la lista entera usa punto decimal
    assert [f["precio"] for f in filas] == [2670.631, 452.111, 371.003]
    assert filas[0]["descripcion"] == "ATUN BAHIA LOMITO NATUR 170GR"


def test_lista_avisa_renglones_que_no_pudo_leer():
    _, no_leidos = renglones_de_texto(["CUBE TRUMPET RESERV CABERN SAUVIGN 7814.282 6 NUEVO"])
    assert len(no_leidos) == 1


def test_lista_con_descuento_usa_el_neto():
    filas, _ = renglones_de_texto([
        "7795933000502 01-0112 Atún Lomitos al Natural (Ecuador) 88 48 x 170 grs. 2,226.21 18.78% 1,808.13",
        "7795933000298 01-2013 Atún Desmenuzado al natural (Ecuador) 88 48 x 170 grs. 1,136.22 18.78% 5.00% 876.70",
        "7795933803257 02-0147 Palmitos en Rodajas (Bolivia) 80 12 x 800 grs. 4,732.95 18.78% 10.00 3,459.69",
        "7795933000045 01-0172 Caballa al Natural (Nacional) SIN STOCK 90 24 x 380 gr. 0.00 18.78% 0.00",
        "7795933803141 O4-2001 Sidra Luna Negra 88 6 x 750 cc. 5,790.00 19.09% 4,684.69",
    ])
    assert [f["precio"] for f in filas] == [1808.13, 876.70, 3459.69, 0.0, 4684.69]
    assert filas[0]["codigo"] == "01-0112"
    assert "88" not in filas[0]["descripcion"].split()        # bultos por pallet, no es el producto
    assert filas[3]["sin_stock"] and "STOCK" not in filas[3]["descripcion"]


def test_marca_del_titulo_de_la_seccion():
    from core.providers import indice_proveedores
    idx = indice_proveedores(construir(export_df()))
    filas, _ = renglones_de_texto(["DISTRIBUIDORA S.A.", "PASTAS RIO",
                                   "01-0001 Fideos naturales 48 x 500 grs. 3,000.00 10.00% 2,700.00"], idx)
    assert filas[0]["marca_seccion"] == "Pastas Río"


# ---------- listas en un ciclo ----------
def test_lista_primera_vez_pregunta_y_despues_entra_sola(catalogo, memoria):
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3650.000 20",
              "ZZ-0001 LAMPARA LED 9W 1500.000 10")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["listos"].empty
    assert list(res["confirmar"].referencia) == ["PAS001"]
    assert len(res["fuera_de_catalogo"]) == 1                    # la lámpara no se pregunta

    memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "PAS001")
    res = correr_fuentes(catalogo, memoria, [f])
    r = res["listos"].iloc[0]
    assert r["referencia"] == "PAS001" and r["via"] == "código de la lista"
    assert r["fuente"] == "Distribuidora Uno" and r["codigo"] == "AL-0100"


def test_codigo_marcado_como_no_se_compra_se_ignora(catalogo, memoria):
    memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "")
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3650.000 20")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["analisis"].iloc[0]["estado"] == "ignorado"


def test_impuestos_de_la_distribuidora(catalogo, memoria):
    memoria.agregar("impuestos", {"ambito": "proveedor", "clave": "Distribuidora Uno",
                                  "iva": "0.21", "percepcion": "0", "nota": ""})
    memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "PAS001")
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3000.000 20")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["listos"].iloc[0]["costo_nuevo"] == pytest.approx(3630.0)


def test_lista_sin_marca_en_la_descripcion_usa_la_de_la_seccion(catalogo, memoria):
    f = lista("Distribuidora Dos", "PASTAS RIO",
              "01-0001 Fideos naturales 48 x 500 grs. 4,000.00 10.00% 3,600.00")
    res = correr_fuentes(catalogo, memoria, [f])
    assert list(res["confirmar"].referencia) == ["PAS001"]


def test_sin_stock_de_algo_que_se_compra_es_un_aviso(catalogo, memoria):
    memoria.aprender_codigo("Distribuidora Dos", "01-0001", "Fideos", "PAS001")
    f = lista("Distribuidora Dos", "01-0001 Fideos naturales SIN STOCK 48 x 500 grs. 0.00 10.00% 0.00",
              "01-0002 Otra cosa SIN STOCK 48 x 500 grs. 0.00 10.00% 0.00")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["listos"].empty and res["confirmar"].empty
    assert list(res["avisos"].referencia) == ["PAS001"]


# ---------- ficha de proveedores ----------
def test_proveedor_nuevo_queda_guardado_y_se_reconoce(memoria):
    assert memoria.registrar_proveedor("Regional  Trade", tipo="lista") == "Regional Trade"
    assert memoria.registrar_proveedor("regional trade") == "Regional Trade"
    assert memoria.nombres_proveedores() == ["Regional Trade"]
    df = memoria.leer("proveedores")
    df.loc[0, "alias"] = "RT|Regional"
    memoria.guardar("proveedores", df)
    assert memoria.buscar_proveedor("RT") == "Regional Trade"


def test_iva_de_la_ficha_se_aplica(catalogo, memoria):
    memoria.fijar_iva("Distribuidora Uno", "21")
    memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "PAS001")
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3000.000 20")
    assert correr_fuentes(catalogo, memoria, [f])["listos"].iloc[0]["costo_nuevo"] == pytest.approx(3630.0)
    memoria.fijar_iva("Distribuidora Uno", "incluido")          # cambiar de opinión no deja la regla vieja
    assert correr_fuentes(catalogo, memoria, [f])["listos"].iloc[0]["costo_nuevo"] == pytest.approx(3000.0)


def test_lista_que_cotiza_el_bulto(catalogo, memoria):
    """'10 x 500 GR $35.000' contra $3.500 en Odoo: es el bulto de 10."""
    memoria.aprender_codigo("Distribuidora Tres", "46001", "FIDEOS", "PAS001")
    f = lista("Distribuidora Tres", "46001 FIDEOS NATURALES PASTAS RIO 10 x 500 GR 36750.000")
    c = correr_fuentes(catalogo, memoria, [f])["confirmar"].iloc[0]
    assert c["alerta"] == "variación fuerte" and c["sugerir_bulto"] == 10
    assert c["costo_por_unidad"] == pytest.approx(3675.0)

    memoria.aprender_codigo("Distribuidora Tres", "46001", "FIDEOS", "PAS001", unidades=10)
    r = correr_fuentes(catalogo, memoria, [f])["listos"].iloc[0]
    assert r["costo_nuevo"] == pytest.approx(3675.0) and r["unidades_bulto"] == 10
    # volver a confirmar el producto no pierde lo del bulto
    memoria.aprender_codigo("Distribuidora Tres", "46001", "FIDEOS", "PAS001")
    assert memoria.unidades_de("Distribuidora Tres") == {"46001": 10}


def test_lista_vieja_avisa_y_reciente_no(catalogo, memoria):
    import datetime as dt

    memoria.aprender_codigo("Distribuidora Tres", "46001", "FIDEOS", "PAS001")
    vieja = lista("Distribuidora Tres", "46001 FIDEOS NATURALES PASTAS RIO 500 GR 3650.000",
                  fecha=dt.date.today() - dt.timedelta(days=61))
    res = correr_fuentes(catalogo, memoria, [vieja])
    assert "tiene 2 meses" in res["fuentes"][0]["aviso_fecha"]

    reciente = lista("Distribuidora Tres", "46001 FIDEOS NATURALES PASTAS RIO 500 GR 3650.000",
                     fecha=dt.date.today() - dt.timedelta(days=5))
    res = correr_fuentes(catalogo, memoria, [reciente])
    assert "aviso_fecha" not in res["fuentes"][0]


def test_columna_id_articulo_no_se_confunde_con_descripcion():
    """'ID Artículo' contiene la palabra 'artículo', que también describe la columna de
    descripción. Sin distinguirlas, la columna del código quedaba sin detectar y el número
    de artículo se leía como si fuera el nombre del producto."""
    cols = _columnas(["ID Artículo", "Descripción", "Marca", "Precio de Venta"])
    assert cols["codigo"] == 0
    assert cols["descripcion"] == 1


def test_lista_de_un_proveedor_nuevo_sin_marca_en_el_texto_igual_pregunta(catalogo, memoria):
    """Negocio recién armado: sin código aprendido y sin haberle comprado nunca nada a esta
    distribuidora, la búsqueda de candidatos quedaba acotada a 'las marcas que ya se le
    compran' -> ninguna -> no buscaba en nada. Debe buscar en todo el catálogo."""
    f = lista("Distribuidora Cinco", "ZZ-0009 LAVANDINA CONCENTRADA 1 LITRO 900.00 20")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["fuera_de_catalogo"].empty
    assert list(res["confirmar"].referencia) == ["LIM001"]


# ---------- fuente habitual ----------
def test_producto_que_se_compra_a_otro_pregunta(catalogo, memoria):
    memoria.registrar_historial([{"fecha": "2026-09-01", "referencia": "PAS001", "producto": "Fideos",
                                  "costo_anterior": 3400, "costo_nuevo": 3500, "fuente": "Pastas Río",
                                  "origen": "planilla", "linea": "", "estado": "verificado"}])
    memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "PAS001")
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3650.000 20")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["listos"].empty
    c = res["confirmar"].iloc[0]
    assert c["alerta"] == "otra fuente" and c["fuente_habitual"] == "Pastas Río"

    res = correr_fuentes(catalogo, memoria, [f], fuente_ok={(c["clave_renglon"], "PAS001")})
    assert list(res["listos"].referencia) == ["PAS001"]


def test_la_fuente_de_un_mensaje_es_quien_lo_manda(catalogo, memoria):
    """Una tabla de un almacén con productos de otra marca: se le compra al almacén."""
    res = correr_fuentes(catalogo, memoria, [planilla(["Almacén Centro", "TRUE",
                                                       "Fideos naturales 500 g\tPastas Río\t$3600"])])
    r = res["analisis"].iloc[0]
    assert r["referencia"] == "PAS001" and r["fuente"] == "Almacén Centro"


# ---------- mensajes con títulos y datos de contacto (caso Miel del Monte) ----------
MIEL_DEL_MONTE = """Hola! Te paso los precios de este mes
Dulce de membrillos 500 grs. $4960
Higos en almibar. 400 grs. 12 unidades aprox.$7800
Dulce de mandarina $3150
Dulce de naranja $3150
Miel en envases de plástico.
x250cm3 $3200
x360cm3 $4500
Miel en frascos de vidrio.
200 cm3 (1/4 kilo) $3290.
360 cm3 (1/2 kilo) $5750.
650 cm3 ( 1 kg ) $8300.
Pedidos: 3415550000
Consultar productos en envase retornable."""


def test_titulo_sin_precio_se_aplica_a_los_renglones_de_abajo():
    from core.parse import extraer_renglones, leer_bloques
    renglones, _ = extraer_renglones(leer_bloques(MIEL_DEL_MONTE, {}, inicial="Miel del Monte"))
    por_precio = {r["costo_nuevo"]: r["descripcion"] for r in renglones}
    assert por_precio[7800.0].startswith("Higos en almibar")           # "$7800" pegado a "aprox."
    assert por_precio[4960.0] == "Dulce de membrillos 500 grs"          # los higos no son variante del dulce
    assert por_precio[3150.0] in ("Dulce de mandarina", "Dulce de naranja")
    assert por_precio[3200.0] == "Miel en envases de plástico x250cm3"
    assert por_precio[4500.0] == "Miel en envases de plástico x360cm3"
    assert por_precio[5750.0] == "Miel en frascos de vidrio 360 cm3 (1/2 kilo"
    assert por_precio[8300.0] == "Miel en frascos de vidrio 650 cm3 ( 1 kg"
    # ni el teléfono ni "Consultar productos..." son productos
    assert all("3415550000" not in r["linea"] and "Consultar" not in r["linea"] for r in renglones)
    assert len(renglones) == 9


def test_solo_presentacion():
    from core.parse import solo_presentacion
    assert solo_presentacion("x250cm3") and solo_presentacion("200 cm3 (1/4 kilo)")
    assert solo_presentacion("650 cm3 ( 1 kg )")
    assert not solo_presentacion("Dulce de naranja") and not solo_presentacion("Miel 500 g")


# ---------- mensajes ya procesados ----------
def test_mensaje_ya_procesado_no_se_vuelve_a_analizar(catalogo, memoria):
    p = planilla(["Pastas Río", "TRUE", "Fideos naturales 500 g $3600"],
                 ["Especias Luna", "FALSE", ""])
    res = correr_fuentes(catalogo, memoria, [p])
    assert len(res["listos"]) == 1
    for h in res["huellas"]:
        memoria.marcar_procesado(h["fuente"], h["huella"], h["origen"], "2026-09-23")

    res = correr_fuentes(catalogo, memoria, [p])
    assert res["listos"].empty
    nota = res["notas"][res["notas"].proveedor == "Pastas Río"].iloc[0]
    assert "ya procesado el 2026-09-23" in nota["nota"]
    assert res["fuentes"][0]["ya_procesados"] == 1

    # "analizar igual"
    res = correr_fuentes(catalogo, memoria, [p], forzados={nota["huella"]})
    assert len(res["listos"]) == 1


def test_mensaje_editado_es_nuevo(catalogo, memoria):
    p1 = planilla(["Pastas Río", "TRUE", "Fideos naturales 500 g $3600"])
    for h in correr_fuentes(catalogo, memoria, [p1])["huellas"]:
        memoria.marcar_procesado(h["fuente"], h["huella"], h["origen"], "2026-09-23")
    p2 = planilla(["Pastas Río", "TRUE", "Fideos naturales 500 g $3700"])
    assert len(correr_fuentes(catalogo, memoria, [p2])["listos"]) == 1


def test_mismo_producto_en_dos_fuentes_gana_la_ultima(catalogo, memoria):
    p = planilla(["Pastas Río", "TRUE", "Fideos naturales 500 g $3600"])
    m = {"tipo": "mensaje", "proveedor": "Pastas Río", "texto": "Fideos naturales 500 g $3650", "nombre": "corrección"}
    res = correr_fuentes(catalogo, memoria, [p, m])
    assert list(res["listos"].costo_nuevo) == [3650.0]
    perdedor = res["confirmar"].iloc[0]
    assert perdedor["costo_nuevo"] == 3600.0 and perdedor["gana_costo"] == 3650.0
    # "usar este": se saltea el ganador y entra el otro
    res = correr_fuentes(catalogo, memoria, [p, m], saltados={perdedor["gana"]})
    assert list(res["listos"].costo_nuevo) == [3600.0]


def test_mensaje_suelto_con_proveedor_elegido(catalogo, memoria):
    res = correr_fuentes(catalogo, memoria, [{"tipo": "mensaje", "proveedor": "Pastas Río",
                                              "texto": "Hola! los canelones de verdura x 6 $9500",
                                              "nombre": "suelto"}])
    r = res["listos"].iloc[0]
    assert r["referencia"] == "PAS004" and r["fuente"] == "Pastas Río"


# ---------- Google Sheets ----------
def test_url_de_sheets_a_csv():
    u = sheets.url_csv("https://docs.google.com/spreadsheets/d/1AbC_x-9/edit#gid=12345")
    assert u == "https://docs.google.com/spreadsheets/d/1AbC_x-9/export?format=csv&gid=12345"
    with pytest.raises(ErrorDeDatos):
        sheets.url_csv("https://example.com/planilla")


class _Resp(io.BytesIO):
    def __init__(self, cuerpo, tipo):
        super().__init__(cuerpo)
        self.headers = {"Content-Type": tipo}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_sheets_no_compartida_da_mensaje_claro():
    abrir = lambda url, timeout: _Resp(b"<!DOCTYPE html><html>login</html>", "text/html")
    with pytest.raises(ErrorDeDatos, match="Cualquier persona con el enlace"):
        sheets.descargar("https://docs.google.com/spreadsheets/d/abc/edit", abrir=abrir)


def test_libro_con_tres_pestanas():
    html = ('{name: "Mensajes", pageUrl: "https://x/htmlview?gid=0"} '
            '{name: "Lista", pageUrl: "https://x/htmlview?gid=111"} '
            '{name: "Manual", pageUrl: "https://x/htmlview?gid=222"}').encode()
    csvs = {"0": b"Proveedor,Mensaje\nPastas R\xc3\xado,Fideos $1\nOtro,\n",
            "111": "Proveedor,Nos envía:\nDistribuidora Uno,Lista\n".encode(),
            "222": b"Proveedor,Chequeado\nVerduleria,TRUE\nCarniceria,FALSE\n"}

    def abrir(url, timeout):
        if "htmlview" in url:
            return _Resp(html, "text/html")
        gid = url.split("gid=")[1] if "gid=" in url else "0"
        return _Resp(csvs[gid], "text/csv")

    libro = sheets.leer_libro("https://docs.google.com/spreadsheets/d/abc/edit#gid=0", abrir=abrir)
    assert libro["pestanas"] == ["Mensajes", "Lista", "Manual"]
    assert libro["listas"] == ["Distribuidora Uno"]
    assert libro["manual"] == [("Verduleria", True), ("Carniceria", False)]


def test_planilla_sin_chequeado_y_avisos_de_pestanas(catalogo, memoria):
    buf = io.StringIO()
    pd.DataFrame([["Pastas Río", "Fideos naturales 500 g $3600"], ["Especias Luna", ""]],
                 columns=["Proveedor", "Mensaje"]).to_csv(buf, index=False)
    f = {"tipo": "planilla", "nombre": "sheets", "contenido": buf.getvalue().encode(),
         "listas_esperadas": ["Distribuidora Uno"], "manual": [("Verduleria", False)]}
    res = correr_fuentes(catalogo, memoria, [f])
    notas = dict(zip(res["notas"].proveedor, res["notas"].nota))
    assert notas["Especias Luna"] == "sin mensaje en la planilla"       # no "mantiene precios"
    assert "todavía no se cargó" in notas["Distribuidora Uno"]
    assert "falta chequear" in notas["Verduleria"]
    assert len(res["listos"]) == 1


def test_sheets_devuelve_el_csv():
    abrir = lambda url, timeout: _Resp(b"Proveedor,Chequeado,Mensaje\n", "text/csv")
    assert sheets.descargar("https://docs.google.com/spreadsheets/d/abc/edit", abrir=abrir).startswith(b"Proveedor")


# ---------- control y verificación ----------
def test_control_margen_fijo_estima_el_precio_de_venta(memoria):
    df = export_df()
    df["list_price"] = ["5000"] * len(df)
    res = correr_fuentes(construir(df), memoria, [planilla(["Pastas Río", "TRUE", "Fideos naturales 500 g $3850"])])
    fila = control(res).iloc[0]
    # el costo sube 10% (3500 -> 3850): el precio de venta estimado sube lo mismo
    assert fila["precio_venta_estimado"] == pytest.approx(5500.0)
    assert fila["margen_%"] == pytest.approx(30.0) and fila["revisar"] == ""
    assert fila["fuente"] == "Pastas Río"


def test_control_margen_variable_compara_contra_el_precio_de_hoy(memoria):
    df = export_df()
    df["list_price"] = ["3000"] * len(df)
    res = correr_fuentes(construir(df), memoria, [planilla(["Pastas Río", "TRUE", "Fideos naturales 500 g $3600"])])
    fila = control(res, margen_variable={"Pastas Río"}).iloc[0]
    assert "precio de venta" in fila["revisar"] and fila["precio_venta_estimado"] == 3000


# ---------- IVA según el producto ----------
def test_proveedor_mixto_pregunta_el_iva_de_cada_producto_una_vez(catalogo, memoria):
    memoria.fijar_iva("Distribuidora Uno", "mixto+3")
    memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "PAS001")
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3000.000 20")
    res = correr_fuentes(catalogo, memoria, [f])
    c = res["confirmar"].iloc[0]
    assert res["listos"].empty
    assert c["alerta"] == "falta IVA" and c["iva_sugerido"] == 0.21 and c["percepcion"] == 0.03

    memoria.fijar_iva_producto("PAS001", 0.105, 0.03)
    r = correr_fuentes(catalogo, memoria, [f])["listos"].iloc[0]
    assert r["costo_nuevo"] == pytest.approx(3000 * 1.105 * 1.03, abs=0.01)


def test_mixto_primero_el_producto_despues_el_iva(catalogo, memoria):
    """Un artículo que todavía no se sabe qué producto es no pregunta el IVA ni entra al archivo."""
    memoria.fijar_iva("Distribuidora Uno", "mixto")
    f = lista("Distribuidora Uno", "AL-0100 FIDEOS NATURALES PASTAS RIO 500 GR 3000.000 20")
    res = correr_fuentes(catalogo, memoria, [f])
    assert res["listos"].empty
    assert res["confirmar"].iloc[0]["alerta"] in ("match a confirmar", "match dudoso")


def test_sugerencia_de_iva_reducido():
    from core.transform import sugerir_iva
    assert sugerir_iva("Harina 000 1 kg - Federación") == 0.105
    assert sugerir_iva("Yerba mate 1 kg - Federación") == 0.21


def test_verificacion_detecta_costo_no_aplicado_y_duplicados():
    antes = construir(export_df())
    esperado = pd.DataFrame({"id": ["__export__.product_template_3_ab12cd34", "__export__.product_template_6_ab12cd34"],
                             "default_code": ["PAS001", "PAS004"], "name": ["a", "b"],
                             "standard_price": [3600.0, 9500.0]})
    df = export_df()
    df.loc[3, "standard_price"] = "3600"            # PAS001 quedó bien; PAS004 no se actualizó
    dup = df.iloc[[3]].copy()
    dup["id"] = "__export__.product_template_999_ffffffff"
    despues = construir(pd.concat([df, dup], ignore_index=True), quitar_repetidos=False)
    v = verificar(esperado, antes, despues)
    assert v["ok"] == 1 and not v["todo_bien"]
    assert v["problemas"].iloc[0]["estado"] == "no se actualizó"
    assert len(v["nuevos"]) == 1


def test_verificacion_todo_bien():
    antes = construir(export_df())
    df = export_df()
    df.loc[3, "standard_price"] = "3600"
    esperado = pd.DataFrame({"id": ["__export__.product_template_3_ab12cd34"], "default_code": ["PAS001"],
                             "name": ["a"], "standard_price": [3600.0]})
    v = verificar(esperado, antes, construir(df))
    assert v["todo_bien"] and len(v["otros_cambios"]) == 0
