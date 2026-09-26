"""El negocio de ejemplo (datos inventados): el recorrido que haría alguien que la prueba.

Primer ciclo: la app resuelve sola lo que puede y pregunta el resto. Se responde como lo
haría una persona. Segundo ciclo con los mismos archivos: entra todo solo, a +10%.
"""
import shutil
from pathlib import Path

import pytest

from core.catalog import construir
from core.cycle import correr_fuentes, fuente_de_archivo
from core.memory import Memoria

EJEMPLO = Path(__file__).resolve().parents[1] / "ejemplo"


@pytest.fixture
def negocio(tmp_path):
    shutil.copytree(EJEMPLO / "memoria", tmp_path / "memoria")
    mem = Memoria(tmp_path / "memoria")
    cat = construir(EJEMPLO / "export_odoo.csv", no_son_productos=mem.no_son_productos())
    fuentes = [fuente_de_archivo("planilla_mensajes.csv", (EJEMPLO / "planilla_mensajes.csv").read_bytes())]
    for proveedor, archivo in (("Distribuidora Norte", "lista Distribuidora Norte.pdf"),
                               ("Distribuidora Sur", "lista Distribuidora Sur.xlsx")):
        fuentes.append({"tipo": "lista", "nombre": archivo, "proveedor": proveedor,
                        "contenido": (EJEMPLO / archivo).read_bytes()})
    return mem, cat, fuentes


def responder(res, mem, saltados):
    """Lo que haría una persona con cada tarjeta. Devuelve cuántas respondió."""
    n = 0
    for r in res["confirmar"].to_dict("records"):
        if r["alerta"] in ("match a confirmar", "match dudoso"):
            ref = r["opciones"][0]["referencia"] if r["opciones"] else r["referencia"]
            if r.get("es_lista") is True:
                mem.aprender_codigo(r["proveedor_bloque"], r["codigo"], r["descripcion"], ref)
            else:
                mem.aprender_alias(r["descripcion"], ref, r["proveedor"])
        elif r["alerta"] == "falta IVA":
            mem.fijar_iva_producto(r["referencia"], 0.105 if "Harina" in r["nombre_odoo"] else 0.21, 0)
        elif r["alerta"] == "variación fuerte" and r.get("sugerir_bulto") == r.get("sugerir_bulto"):
            mem.aprender_codigo(r["proveedor_bloque"], r["codigo"], r["descripcion"], r["referencia"],
                                unidades=int(r["sugerir_bulto"]))
        else:                       # la canela viene en otra presentación: este mes no
            saltados.add(r["clave_renglon"])
        n += 1
    return n


def test_primer_ciclo_resuelve_lo_seguro_y_pregunta_el_resto(negocio):
    mem, cat, fuentes = negocio
    res = correr_fuentes(cat, mem, fuentes)
    assert [f["no_leidos"] for f in res["fuentes"]] == [[], [], []]
    assert len(res["listos"]) == 15
    assert set(res["listos"]["variacion_%"]) == {10.0}               # todo lo automático, a +10%
    assert len(res["confirmar"]) == 17
    assert set(res["avisos"].linea) == {"Canelones igual", "Sin quesos azules"}
    assert len(res["fuera_de_catalogo"]) == 7                          # lo que las listas traen y no se vende
    assert "Cuota social mensual" not in set(cat.nombre_completo)


def test_segundo_mes_entra_solo(negocio):
    mem, cat, fuentes = negocio
    saltados = set()
    for _ in range(4):              # producto -> IVA -> bulto: a lo sumo unas vueltas
        res = correr_fuentes(cat, mem, fuentes, saltados=saltados)
        if not responder(res, mem, saltados):
            break
    res = correr_fuentes(cat, mem, fuentes, saltados=saltados)
    assert res["confirmar"].empty
    assert len(res["listos"]) == 31
    variaciones = dict(zip(res["listos"].referencia, res["listos"]["variacion_%"]))
    assert variaciones.pop("PAN005") == pytest.approx(3.5)            # los budines vienen al mismo precio
    assert set(round(v) for v in variaciones.values()) == {10}
    c = dict(zip(res["listos"].referencia, res["listos"].costo_nuevo))
    assert c["HAR001"] == pytest.approx(880, abs=0.5)                  # harina al 10,5%
    assert c["ACE001"] == pytest.approx(2860, abs=0.5)                 # aceite: el bulto de 12, dividido
    assert c["CON001"] == pytest.approx(2310, abs=0.5)                 # neto de la lista + 21%
