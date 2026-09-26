"""Regresión contra el ciclo real de septiembre.

Los datos reales no viajan con el repo: si no están, el test se saltea.
El export es posterior a la importación de septiembre, así que reconstruir exacto el
costo que ya está en Odoo es la verificación (sección 3.6 del handoff).
"""
from pathlib import Path

import pytest

from core.cycle import correr_desde_archivos
from core.memory import Memoria

RAIZ = Path(__file__).resolve().parents[1]
ENTRADA = RAIZ.parent / "Actualizador de costos Odoo" / "entrada"
EXPORT = ENTRADA / "Producto (product.template) (4).csv"
MENSAJES = ENTRADA / "ejemplo_ciclo_septiembre.csv"
MEMORIA = RAIZ / "datos" / "memoria"

pytestmark = pytest.mark.skipif(not (EXPORT.exists() and MENSAJES.exists()
                                     and (RAIZ.parent / "Actualizador de costos Odoo" / "toolkit").exists()),
                                reason="no están los datos reales")


def test_septiembre_reproduce_los_costos_de_odoo(tmp_path):
    res = correr_desde_archivos(EXPORT, [MENSAJES], _con_memoria(tmp_path))
    estados = res["analisis"].estado.value_counts().to_dict()
    assert estados.get("sin cambio") == 116
    assert estados.get("ignorado") == 12
    assert estados.get("aviso") == 9
    assert len(res["listos"]) == 0     # ya estaba todo importado


TOOLKIT = RAIZ.parent / "Actualizador de costos Odoo" / "toolkit"


def _con_memoria(tmp_path):
    """La memoria del prototipo tal como quedó en septiembre (no la que se va editando al usar
    la app), con la única corrección de datos que se hizo al portarla."""
    mem = Memoria(tmp_path / "m")
    for p in TOOLKIT.glob("*.csv"):
        (tmp_path / "m" / p.name).write_bytes(p.read_bytes())
    ig = mem.leer("ignorar")
    ig.loc[ig.texto.str.startswith("Yogures Natural"), "proveedor"] = "Cotar"
    mem.guardar("ignorar", ig)
    # la jerga propia que antes estaba en el código y ahora vive en la memoria del negocio
    for t, s in (("masitas", ""), ("masita", ""), ("muesli", "mueslis"), ("bahi", "bahia"), ("andresit", "andresito")):
        mem.agregar("abreviaturas", {"texto": t, "significa": s, "nota": ""})
    for t in ("cuota social", "bono solidario", "faltantes pedidos", "tareas de administracion"):
        mem.agregar("no_son_productos", {"empieza_con": t, "nota": ""})
    return mem


AFA = RAIZ.parent / "2026-09-01 AFA Lista Rosario.pdf"
LISTA7 = RAIZ.parent / "Lista 7 - Varios (15).pdf"


@pytest.mark.skipif(not AFA.exists(), reason="no está el PDF")
def test_lista_afa_en_pdf_igual_que_transcripta(tmp_path):
    """El PDF de AFA da lo mismo que la lista que antes se transcribía a mano."""
    res = correr_desde_archivos(EXPORT, [AFA], _con_memoria(tmp_path),
                                proveedores={str(AFA): "Agricultores Federados"})
    estados = res["analisis"].estado.value_counts().to_dict()
    assert res["fuentes"][0]["articulos"] == 37
    assert estados.get("sin cambio") == 15
    assert estados.get("ignorado") == 21
    assert res["listos"].empty and res["confirmar"].empty


RT = RAIZ.parent / "LISTA REGIONAL TRADE 22 DE SEPTIEMBRE 2026_(1).pdf"


@pytest.mark.skipif(not RT.exists(), reason="no está el PDF")
def test_lista_regional_trade(tmp_path):
    """Lista con código de barras, descuento y neto; la marca (Bahía) está solo en el título."""
    mem = _con_memoria(tmp_path)
    mem.fijar_iva("Regional Trade", "21")                 # "PRECIOS MAS IVA"
    res = correr_desde_archivos(EXPORT, [RT], mem, proveedores={str(RT): "Regional Trade"})
    assert res["fuentes"][0]["articulos"] >= 90 and not res["fuentes"][0]["no_leidos"]
    c = res["confirmar"].set_index("codigo")
    # atún lomito natural 170 g: neto 1.808,13 + 21% de IVA
    assert c.loc["01-0112", "referencia"] == "ALM16001"
    assert c.loc["01-0112", "costo_nuevo"] == pytest.approx(1808.13 * 1.21, abs=0.01)


@pytest.mark.skipif(not LISTA7.exists(), reason="no está el PDF")
def test_lista_grande_solo_pregunta_lo_que_se_vende(tmp_path):
    res = correr_desde_archivos(EXPORT, [LISTA7], _con_memoria(tmp_path),
                                proveedores={str(LISTA7): "Distribuidora Gusmano"})
    assert res["fuentes"][0]["articulos"] > 1200
    assert len(res["confirmar"]) <= 20
    assert res["listos"].empty                      # por nombre nunca entra solo la primera vez
    assert "ALM16001" in set(res["confirmar"].referencia)   # atún Bahía lomito natural
