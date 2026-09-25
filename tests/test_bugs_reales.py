"""Un test por cada bug real que tuvo el prototipo (sección 3 del handoff)."""
import pandas as pd
import pytest

from core import ErrorDeDatos
from core.catalog import construir
from core.normalize import normalizar
from core.output import escribir_import, verificar_ids
from core.parse import fila_de_factura

from conftest import export_df


def filas(res, hoja, ref):
    df = res[hoja]
    return df[df.referencia == ref] if len(df) else df


# 1. Export sin columna id -> aborta con mensaje claro
def test_export_sin_id_aborta():
    df = export_df().drop(columns=["id"])
    with pytest.raises(ErrorDeDatos, match="Quiero actualizar datos"):
        construir(df)


def test_id_no_se_busca_por_fragmento():
    """El bug original: "id" está adentro de "cantIDad a la mano"."""
    df = export_df().drop(columns=["id"]).rename(columns={"qty_available": "Cantidad a la mano"})
    with pytest.raises(ErrorDeDatos):
        construir(df)


# 2. Columna id con valores que no son IDs externos -> no genera el archivo
def test_ids_invalidos_no_generan_archivo(tmp_path, ciclo):
    res = ciclo("Pastas Río\nFideos naturales 500 g $3600")
    assert len(res["listos"]) == 1
    res["listos"].loc[:, "id_externo"] = "7"          # una cantidad de stock, no un ID
    destino = tmp_path / "import.xlsx"
    with pytest.raises(ErrorDeDatos, match="duplicados"):
        escribir_import(res, destino)
    assert not destino.exists()


def test_verificar_ids_acepta_ids_externos():
    verificar_ids(pd.DataFrame({"id": ["__export__.product_template_9_56c50ae4"],
                                "standard_price": [10.0]}), "x")


# 3. Alias de un proveedor no matchea con producto de otro
def test_alias_acotado_a_su_proveedor(ciclo, memoria):
    memoria.aprender_alias("Prepizzas", "DUL001", "Dulces del Sur")
    res = ciclo("Panadería Norte\nPrepizzas $2100")
    d = res["analisis"]
    assert "DUL001" not in set(d.referencia)
    assert d.iloc[0]["referencia"] == "PAN001"


# 4. "c/miel" no matchea con "c/miel y cacao"
def test_subconjunto_no_se_come_al_mas_largo(ciclo, memoria):
    memoria.aprender_alias("c/miel y cacao", "PAS003", "Pastas Río")
    memoria.aprender_alias("c/miel", "PAS002", "Pastas Río")
    res = ciclo("Pastas Río\n- c/miel $3800\n- c/ miel y cacao $4000")
    d = res["analisis"].set_index("costo_nuevo")
    assert d.loc[3800.0, "referencia"] == "PAS002"
    assert d.loc[4000.0, "referencia"] == "PAS003"


def test_alias_mas_largo_no_captura_texto_corto(ciclo, memoria):
    """Con solo el alias largo aprendido, "c/miel" no puede resolverse por alias."""
    memoria.aprender_alias("c/miel y cacao", "PAS003", "Pastas Río")
    res = ciclo("Pastas Río\n- c/miel $3800")
    r = res["analisis"].iloc[0]
    assert not (r["referencia"] == "PAS003" and r["via"] == "alias aprendido")


# 5. "15 canelones" sí matchea con el alias "canelones"
def test_cantidad_de_compra_al_inicio(ciclo, memoria):
    memoria.aprender_alias("canelones", "PAS004", "Pastas Río")
    res = ciclo("Pastas Río\n15 canelones $9500")
    r = res["analisis"].iloc[0]
    assert r["referencia"] == "PAS004" and r["via"] == "alias aprendido"


# 6. 1/2 kg -> 500 g
def test_medio_kilo():
    assert "500g" in normalizar("Ravioles x 1/2 kg")
    assert "2000g" not in normalizar("Ravioles x 1/2 kg")
    assert normalizar("750 g") == normalizar("0,75 kg") == normalizar("750g")


# 7. x 12 -> 12 unidades; x 500 -> 500 gramos
def test_x_n_sin_unidad():
    assert "12u" in normalizar("huevos x 12")
    assert "500g" in normalizar("reggianito x 500")


# 8. Dos renglones al mismo producto -> solo entra el de más confianza
def test_mismo_producto_no_entra_dos_veces(ciclo, memoria):
    memoria.aprender_alias("Fideos naturales", "PAS001", "Pastas Río")
    res = ciclo("Pastas Río\nFideos naturales $3600\nFideos naturales caseros $3650")
    listos = res["listos"]
    assert (listos.referencia == "PAS001").sum() == 1
    assert listos[listos.referencia == "PAS001"].iloc[0]["costo_nuevo"] == 3600
    assert (res["confirmar"].referencia == "PAS001").sum() == 1


# 9. Precio por kilo × peso del nombre
def test_precio_por_kilo(ciclo, memoria):
    memoria.agregar("reglas_proveedor", {"proveedor": "Embutidos Sierra", "cotiza_por": "kilo",
                                         "iva": "incluido", "nota": ""})
    memoria.aprender_alias("BONDIOLA FET", "EMB001", "Embutidos Sierra")
    res = ciclo("Embutidos Sierra\nBONDIOLA FET $22000")
    r = res["listos"].iloc[0]
    assert r["referencia"] == "EMB001"
    assert r["costo_nuevo"] == pytest.approx(22000 * 0.150)


# 10. Importe/Cantidad con descuento
def test_factura_usa_importe_sobre_cantidad():
    desc, costo = fila_de_factura("02 12323 LIMPIA. LAVANDINA 1LTS  C     36    900.00  10.00  29160.00")
    assert costo == 810.0               # 29160 / 36, no los 900 de lista
    assert "LAVANDINA" in desc


def test_factura_en_un_ciclo(ciclo):
    res = ciclo("Limpieza Clara\n02 12323 CLARA. LAVANDINA 1 L     36    900.00  10.00  29160.00")
    r = res["analisis"].iloc[0]
    assert r["costo_nuevo"] == 810.0


# 11. Presentación distinta sin aprobar -> frena el costo
def test_presentacion_distinta_frena(ciclo, memoria):
    res = ciclo("Especias Luna\nOrégano x 25 g $612")
    assert filas(res, "listos", "ESP001").empty
    c = filas(res, "confirmar", "ESP001")
    assert c.iloc[0]["alerta"] == "presentación distinta"


def test_presentacion_aprobada_corrige_nombre(ciclo, memoria):
    memoria.aprobar("ESP001", "gramaje", "pasó de 50 g a 25 g")
    res = ciclo("Especias Luna\nOrégano x 25 g $612")
    assert len(filas(res, "listos", "ESP001")) == 1
    assert res["nombres_corregidos"]["ESP001"].startswith("Orégano 25 g")


# ---- lo propio de cada negocio vive en su memoria, no en el código ----
def test_abreviatura_propia_del_negocio(ciclo, memoria):
    """Una distribuidora escribe 'RIOO' en vez de 'Río': sin enseñarlo no hay match seguro."""
    antes = ciclo("Pastas Río\nFIDEOS NATURALES RIOO 500 G $3600")["analisis"].iloc[0]
    memoria.agregar("abreviaturas", {"texto": "rioo", "significa": "rio", "nota": ""})
    despues = ciclo("Pastas Río\nFIDEOS NATURALES RIOO 500 G $3600")["analisis"].iloc[0]
    assert despues["referencia"] == "PAS001" and despues["score"] >= antes["score"]


def test_filas_que_no_son_productos_propias(memoria):
    df = export_df([("X1", "Cuota social marzo", 100), ("PAS001", "Fideos 500 g - Pastas Río", 3500)])
    assert len(construir(df)) == 2
    assert list(construir(df, no_son_productos=["cuota social"]).referencia) == ["PAS001"]


# ---- redes de seguridad generales ----
def test_import_sin_inventario_ni_publicacion(tmp_path, ciclo):
    res = ciclo("Pastas Río\nFideos naturales 500 g $3600")
    ruta = escribir_import(res, tmp_path / "import.xlsx")
    cols = list(pd.read_excel(ruta).columns)
    assert cols == ["id", "default_code", "name", "standard_price"]


def test_faltante_se_avisa_no_se_despublica(ciclo):
    res = ciclo("Embutidos Sierra\nSin salames")
    assert len(res["avisos"]) >= 1
    assert "EMB002" in set(res["avisos"].referencia)
    assert res["listos"].empty


def test_variacion_fuerte_va_a_confirmar(ciclo):
    res = ciclo("Pastas Río\nFideos naturales 500 g $9000")
    assert res["listos"].empty
    assert res["confirmar"].iloc[0]["alerta"] == "variación fuerte"


def test_variacion_aprobada_en_el_ciclo_entra(ciclo):
    res = ciclo("Pastas Río\nFideos naturales 500 g $9000")
    r = res["confirmar"].iloc[0]
    res2 = ciclo("Pastas Río\nFideos naturales 500 g $9000",
                 aprobados_ciclo={(r["clave_renglon"], r["referencia"])})
    assert len(res2["listos"]) == 1


def test_ignorado_acotado_al_proveedor(ciclo, memoria):
    memoria.aprender_ignorar("Comino 25 g", "Otro Proveedor", "no lo vendemos")
    res = ciclo("Especias Luna\nComino 25 g $700")
    assert res["analisis"].iloc[0]["estado"] != "ignorado"


def test_precio_pegado_al_producto_sin_espacio(ciclo):
    """'harina2000' sin espacio antes del precio no se leía como precio: la línea quedaba
    como una variante sin precio, colgada del producto anterior ('aceite 2500')."""
    res = ciclo("hola\naceite 2500\nharina2000")
    filas = res["analisis"]
    assert len(filas) == 2
    assert dict(zip(filas["descripcion"], filas["costo_nuevo"])) == {"aceite": 2500.0, "harina": 2000.0}
