"""Recorrido completo de la interfaz: subir, revisar, decidir, generar."""
import importlib
import io
import os

import pandas as pd
import pytest

from conftest import export_df


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTUALIZADOR_DATOS", str(tmp_path / "datos"))
    import web.app as modulo
    modulo = importlib.reload(modulo)
    modulo.app.config["TESTING"] = True
    return modulo, modulo.app.test_client()


def subir(c, export, mensajes):
    buf = io.BytesIO()
    export.to_csv(buf, index=False)
    return c.post("/cargar", data={
        "export": (io.BytesIO(buf.getvalue()), "export.csv"),
        "mensajes": (io.BytesIO(mensajes.encode("utf-8")), "planilla.csv"),
    }, content_type="multipart/form-data")


PLANILLA = ('Proveedor,Chequeado,Mensaje\n'
            '"Pastas Río",TRUE,"Fideos naturales 500 g $3600\nCanelones verdura x 6 $15000"\n'
            '"Especias Luna",FALSE,\n')


def test_recorrido_completo(cliente):
    modulo, c = cliente
    r = subir(c, export_df(), PLANILLA)
    assert r.status_code == 302

    pagina = c.get("/revisar").get_data(as_text=True)
    assert "listos para importar" in pagina
    assert "El costo cambia más de un 30%" in pagina          # canelones 9000 -> 15000

    _, res = modulo.resultado()
    fila = res["confirmar"].iloc[0]
    r = c.post("/decidir", data={"clave": fila["clave_renglon"], "accion": "aprobar",
                                 "referencia": fila["referencia"]})
    assert r.status_code == 302
    _, res = modulo.resultado()
    assert len(res["listos"]) == 2 and res["confirmar"].empty

    pagina = c.post("/generar").get_data(as_text=True)
    assert "Archivo listo" in pagina
    generado = next((modulo.SALIDAS).glob("IMPORTAR*.xlsx"))
    imp = pd.read_excel(generado)
    assert list(imp.columns) == ["id", "default_code", "name", "standard_price"]
    assert set(imp.default_code) == {"PAS001", "PAS004"}


def test_export_sin_id_muestra_error_claro(cliente):
    _, c = cliente
    r = subir(c, export_df().drop(columns=["id"]), PLANILLA)
    assert r.status_code == 400
    assert "Quiero actualizar datos" in r.get_data(as_text=True)


def test_mensaje_suelto_y_verificacion(cliente):
    modulo, c = cliente
    buf = io.BytesIO()
    export_df().to_csv(buf, index=False)
    c.post("/export", data={"export": (io.BytesIO(buf.getvalue()), "export.csv")},
           content_type="multipart/form-data")
    c.post("/fuente/mensaje", data={"proveedor": "Pastas Río", "texto": "Fideos naturales 500 g $3600"})
    pagina = c.get("/revisar").get_data(as_text=True)
    assert "Pastas Río" in pagina and "Mensaje de Pastas Río" in pagina

    pagina = c.post("/generar").get_data(as_text=True)
    assert "Mirá la lista una última vez" in pagina
    assert "1 productos con costo nuevo" in pagina
    # volver a la revisión después de generar: lo generado sigue ahí
    _, res = modulo.resultado()
    modulo._cache["resultado"] = None
    _, res = modulo.resultado()
    assert len(res["listos"]) == 1
    hist = modulo.memoria.leer("historial")
    assert list(hist.estado) == ["generado"] and hist.iloc[0]["fuente"] == "Pastas Río"

    # Odoo después de importar: PAS001 con el costo nuevo
    despues = export_df()
    despues.loc[3, "standard_price"] = "3600"
    buf = io.BytesIO()
    despues.to_csv(buf, index=False)
    pagina = c.post("/verificar", data={"export": (io.BytesIO(buf.getvalue()), "despues.csv")},
                    content_type="multipart/form-data").get_data(as_text=True)
    assert "quedaron bien en Odoo" in pagina
    assert list(modulo.memoria.leer("historial").estado) == ["verificado"]

    # ciclo nuevo con el mismo mensaje: ya no es un precio nuevo
    c.post("/nuevo")
    c.post("/export", data={"export": (io.BytesIO(buf.getvalue()), "export.csv")},
           content_type="multipart/form-data")
    c.post("/fuente/mensaje", data={"proveedor": "Pastas Río", "texto": "Fideos naturales 500 g $3600"})
    _, res = modulo.resultado()
    assert res["listos"].empty and "ya procesado" in res["notas"].iloc[0]["nota"]


def test_lista_elegir_aprende_codigo(cliente):
    modulo, c = cliente
    buf = io.BytesIO()
    export_df().to_csv(buf, index=False)
    c.post("/export", data={"export": (io.BytesIO(buf.getvalue()), "export.csv")},
           content_type="multipart/form-data")
    lista = "Codigo;Descripcion;Precio\nAL-0100;FIDEOS NATURALES PASTAS RIO 500 GR;3650,00\n"
    subir_lista = lambda iva: c.post("/fuente/listas", data={
        "proveedor": "Distribuidora Uno", "iva": iva,
        "listas": (io.BytesIO(lista.encode()), "lista.csv")}, content_type="multipart/form-data")
    # proveedor nuevo: la primera vez hay que decir si la lista incluye IVA
    r = subir_lista("")
    assert r.status_code == 400 and "IVA" in r.get_data(as_text=True)
    r = subir_lista("incluido")
    assert "Proveedor nuevo guardado: Distribuidora Uno" in r.get_data(as_text=True)
    assert modulo.memoria.iva_de("distribuidora uno") == "incluido"
    _, res = modulo.resultado()
    fila = res["confirmar"].iloc[0]
    c.post("/decidir", data={"clave": fila["clave_renglon"], "accion": "elegir", "elegido": "PAS001"})
    codigos = modulo.memoria.leer("codigos_proveedor")
    assert list(codigos.referencia_odoo) == ["PAS001"]
    _, res = modulo.resultado()
    assert list(res["listos"].referencia) == ["PAS001"]


def test_ciclo_de_practica_aprende_pero_no_registra(cliente):
    modulo, c = cliente
    c.post("/nuevo", data={"practica": "1", "subir": "10"})
    buf = io.BytesIO()
    export_df().to_csv(buf, index=False)
    c.post("/export", data={"export": (io.BytesIO(buf.getvalue()), "export.csv")},
           content_type="multipart/form-data")
    c.post("/fuente/mensaje", data={"proveedor": "Pastas Río",
                                    "texto": "Fideos naturales 500 g $3500\nLos de la casa $3400"})
    assert "MODO PRÁCTICA" in c.get("/").get_data(as_text=True)

    _, res = modulo.resultado()
    assert list(res["listos"].costo_nuevo) == [3850.0]                 # 3500 + 10% simulado
    duda = res["confirmar"].iloc[0]
    c.post("/decidir", data={"clave": duda["clave_renglon"], "accion": "elegir",
                             "buscado": "Fideos con miel 500 g - Pastas Río"})
    assert (modulo.memoria.leer("alias").referencia == "PAS002").any()  # lo confirmado se aprende

    pagina = c.post("/generar").get_data(as_text=True)
    assert "PRACTICA - NO IMPORTAR" in pagina and "Esto es una práctica" in pagina
    assert "Después de importar" not in pagina
    assert list((modulo.SALIDAS / "practica").glob("PRACTICA - NO IMPORTAR*.xlsx"))
    assert not list(modulo.SALIDAS.glob("IMPORTAR*.xlsx"))
    assert modulo.memoria.leer("historial").empty                      # nada de historial
    assert modulo.memoria.leer("procesados").empty                     # ni mensajes procesados
    assert list((modulo.DATOS / "respaldos").iterdir())                # al empezar se respaldó la memoria


def test_instalacion_nueva_pasa_por_la_bienvenida(cliente):
    modulo, c = cliente
    r = c.get("/")
    assert r.status_code == 302 and "/bienvenida" in r.headers["Location"]
    r = c.post("/bienvenida", data={
        "negocio": "Almacén del Barrio",
        "proveedores": "Distribuidora Norte\nCooperativa Agrícola del Sur - CAS\n\n",
        "archivo_proveedores": (io.BytesIO("Panadería La Espiga\n".encode()), "provs.txt"),
        "url": ""}, content_type="multipart/form-data")
    assert r.status_code == 302
    assert modulo.memoria.nombres_proveedores() == ["Cooperativa Agrícola del Sur", "Distribuidora Norte",
                                                    "Panadería La Espiga"]
    assert modulo.memoria.buscar_proveedor("CAS") == "Cooperativa Agrícola del Sur"
    pagina = c.get("/").get_data(as_text=True)                 # ya no vuelve a la bienvenida
    assert "Almacén del Barrio" in pagina and "Actualizar costos" in pagina


def test_proveedores_se_guardan_todos_juntos(cliente):
    modulo, c = cliente
    for n in ("Uno", "Dos"):
        modulo.memoria.registrar_proveedor(n)
    r = c.post("/proveedores/todos", data={"nombre": ["Dos", "Uno"], "alias": ["D2", ""],
                                            "tipo": ["lista", "mensaje"], "iva": ["mixto+3", ""],
                                            "margen": ["fijo", "variable"]})
    assert r.status_code == 302
    df = modulo.memoria.leer("proveedores").set_index("nombre")
    assert df.loc["Dos", "alias"] == "D2" and df.loc["Dos", "iva_lista"] == "mixto+3"
    assert df.loc["Uno", "tipo"] == "mensaje" and df.loc["Uno", "margen"] == "variable"
    assert modulo.memoria.margen_variable() == {"Uno"}
    imp = modulo.memoria.leer("impuestos")
    assert list(imp.iva) == ["según producto"] and list(imp.percepcion) == ["0.03"]


def test_tarjeta_de_iva_guarda_el_del_producto(cliente):
    modulo, c = cliente
    buf = io.BytesIO()
    export_df().to_csv(buf, index=False)
    c.post("/export", data={"export": (io.BytesIO(buf.getvalue()), "export.csv")},
           content_type="multipart/form-data")
    modulo.memoria.fijar_iva("Distribuidora Uno", "mixto")
    modulo.memoria.aprender_codigo("Distribuidora Uno", "AL-0100", "FIDEOS", "PAS001")
    lista = "Codigo;Descripcion;Precio\nAL-0100;FIDEOS NATURALES PASTAS RIO 500 GR;3000,00\n"
    c.post("/fuente/listas", data={"proveedor": "Distribuidora Uno", "iva": "",
                                   "listas": (io.BytesIO(lista.encode()), "lista.csv")},
           content_type="multipart/form-data")
    pagina = c.get("/revisar").get_data(as_text=True)
    assert "qué IVA lleva este producto" in pagina and "IVA 10,5%" in pagina
    _, res = modulo.resultado()
    fila = res["confirmar"].iloc[0]
    c.post("/decidir", data={"clave": fila["clave_renglon"], "accion": "iva_producto",
                             "referencia": "PAS001", "iva": "0.105"})
    _, res = modulo.resultado()
    assert res["listos"].iloc[0]["costo_nuevo"] == pytest.approx(3315.0)


def test_elegir_producto_aprende_alias(cliente):
    modulo, c = cliente
    subir(c, export_df(), 'Proveedor,Chequeado,Mensaje\n"Pastas Río",TRUE,"Los de la casa $3800"\n')
    _, res = modulo.resultado()
    fila = res["confirmar"].iloc[0]
    c.post("/decidir", data={"clave": fila["clave_renglon"], "accion": "elegir",
                             "buscado": "Fideos con miel 500 g - Pastas Río"})
    alias = modulo.memoria.leer("alias")
    assert (alias.referencia == "PAS002").any()
    _, res = modulo.resultado()
    assert list(res["listos"].referencia) == ["PAS002"]


def test_elegir_por_buscador_no_falla_por_mayusculas_o_tildes(cliente):
    """El buscador de 'no es este producto' comparaba el texto tipeado contra el catálogo
    letra por letra: si no coincidía EXACTO (mayúsculas, tildes, un espacio de más porque
    se tipeó a mano en vez de clickear la sugerencia del navegador), la elección se perdía
    en silencio, sin avisar nada, y la tarjeta volvía a aparecer sin cambios."""
    modulo, c = cliente
    subir(c, export_df(), 'Proveedor,Chequeado,Mensaje\n"Pastas Río",TRUE,"Los de la casa $3800"\n')
    _, res = modulo.resultado()
    fila = res["confirmar"].iloc[0]
    c.post("/decidir", data={"clave": fila["clave_renglon"], "accion": "elegir",
                             "buscado": "FIDEOS  CON MIEL 500 G - PASTAS RIO"})
    alias = modulo.memoria.leer("alias")
    assert (alias.referencia == "PAS002").any()
    _, res = modulo.resultado()
    assert list(res["listos"].referencia) == ["PAS002"]


# ---- el caso "leches-2000": la app sugiere el dulce de leche y es la leche ----
LACTEOS = [("DUL001", "Dulce de leche La Estancia 1 kg - Lácteos del Sur", 3500),
           ("LEC001", "Leche entera 1 l - Lácteos del Sur", 1900),
           ("YOG001", "Yogur bebible 1 l - Lácteos del Sur", 1200)]


def cargar_lacteos(c, con_referencia=True):
    df = export_df(LACTEOS)
    if not con_referencia:
        df["default_code"] = ""
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    c.post("/export", data={"export": (io.BytesIO(buf.getvalue()), "export.csv")},
           content_type="multipart/form-data")
    c.post("/fuente/mensaje", data={"proveedor": "Lácteos del Sur", "texto": "dulce leches-2000\nyogur bebible 1300"})


def tarjeta_de(modulo, texto):
    _, res = modulo.resultado()
    return next(t for t in res["confirmar"].to_dict("records") if texto in t["descripcion"])


def id_de(modulo, nombre):
    cat = modulo.catalogo(modulo.leer_estado())
    return cat.set_index("nombre_completo").loc[nombre, "referencia"]


@pytest.mark.parametrize("con_referencia", [True, False])
def test_elegir_otro_producto_que_el_sugerido(cliente, con_referencia):
    """Muchos negocios no cargan la Referencia interna en Odoo. La app identificaba cada
    producto solo por ella: sin ella, todas las opciones mandaban el mismo valor vacío y
    "Es este producto" no hacía nada; solo funcionaba "No lo vendemos". Además, todos los
    productos sin referencia contaban como el mismo, así que se pisaban entre sí al armar
    el archivo. Ahora, sin Referencia interna, la app usa el ID externo por dentro."""
    modulo, c = cliente
    cargar_lacteos(c, con_referencia)
    t = tarjeta_de(modulo, "dulce leches")
    assert t["nombre_odoo"].startswith("Dulce de leche")           # la sugerencia equivocada
    leche = id_de(modulo, "Leche entera 1 l - Lácteos del Sur")
    assert leche                                                   # nunca vacío
    c.post("/decidir", data={"clave": t["clave_renglon"], "accion": "elegir", "elegido": leche})
    t = tarjeta_de(modulo, "yogur")
    c.post("/decidir", data={"clave": t["clave_renglon"], "accion": "aprobar", "referencia": t["referencia"]})

    _, res = modulo.resultado()
    assert res["confirmar"].empty
    assert sorted(res["listos"].nombre_odoo) == ["Leche entera 1 l - Lácteos del Sur", "Yogur bebible 1 l - Lácteos del Sur"]
    c.post("/generar")
    imp = pd.read_excel(next(modulo.SALIDAS.glob("IMPORTAR*.xlsx")), dtype=str).fillna("")
    # a Odoo va la Referencia interna real: vacía si no la tiene, nunca el ID de adentro
    esperado = {"LEC001", "YOG001"} if con_referencia else {""}
    assert set(imp.default_code) == esperado
    assert set(imp.id) == {"__export__.product_template_1_ab12cd34", "__export__.product_template_2_ab12cd34"}


def test_aprobar_costo_confirma_tambien_el_producto(cliente):
    """Si la app no estaba segura del producto Y el costo cambiaba mucho, la tarjeta solo
    decía "El costo cambia más de un 30%". Aprobar sacaba esa alerta, pero la tarjeta volvía
    a aparecer ("Revisalo antes de importar"): parecía que el botón no hacía nada. Ahora la
    tarjeta avisa que duda del producto, y aprobar confirma las dos cosas, y se aprende."""
    modulo, c = cliente
    cargar_lacteos(c)
    pagina = c.get("/revisar").get_data(as_text=True)
    assert "No estoy seguro de que sea este producto. El costo cambia" in pagina
    t = tarjeta_de(modulo, "dulce leches")
    c.post("/decidir", data={"clave": t["clave_renglon"], "accion": "aprobar", "referencia": t["referencia"]})
    _, res = modulo.resultado()
    assert "DUL001" in set(res["listos"].referencia)
    assert not any("dulce leches" in d for d in res["confirmar"].descripcion)
    assert (modulo.memoria.leer("alias").referencia == "DUL001").any()


def test_buscador_sin_resultado_no_confirma_la_sugerencia_marcada(cliente):
    """Con el buscador escrito pero sin encontrar nada, se confirmaba la opción que venía
    marcada, que es justamente la sugerencia que la persona estaba corrigiendo: la app
    aprendía "leches = dulce de leche". Ahora no se guarda nada y se avisa en pantalla."""
    modulo, c = cliente
    cargar_lacteos(c)
    t = tarjeta_de(modulo, "dulce leches")
    r = c.post("/decidir", data={"clave": t["clave_renglon"], "accion": "elegir",
                                 "elegido": t["referencia"], "buscado": "leche de almendras"},
               follow_redirects=True)
    assert "No encontré “leche de almendras”" in r.get_data(as_text=True)
    assert modulo.memoria.leer("alias").empty
    assert tarjeta_de(modulo, "dulce leches")["referencia"] == "DUL001"
