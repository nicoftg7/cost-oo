import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.catalog import construir  # noqa: E402
from core.memory import Memoria  # noqa: E402
from core.cycle import correr  # noqa: E402

PRODUCTOS = [
    # (referencia, nombre, costo)
    ("PAN001", "Prepizza napolitana - Panadería Norte", 2000),
    ("PAN002", "Pan de campo 500 g - Panadería Norte", 3000),
    ("DUL001", "Prepizzas integrales x 2 - Dulces del Sur", 5500),
    ("PAS001", "Fideos naturales 500 g - Pastas Río", 3500),
    ("PAS002", "Fideos con miel 500 g - Pastas Río", 3700),
    ("PAS003", "Fideos con miel y cacao 500 g - Pastas Río", 3900),
    ("PAS004", "Canelones de verdura x 6 - Pastas Río", 9000),
    ("EMB001", "Bondiola feteada 150 g - Embutidos Sierra", 3200),
    ("EMB002", "Salame picado grueso 300 g - Embutidos Sierra", 5900),
    ("ESP001", "Orégano 50 g - Especias Luna", 600),
    ("ESP002", "Comino 25 g - Especias Luna", 640),
    ("LIM001", "Lavandina 1 l - Limpieza Clara", 850),
]


def export_df(productos=PRODUCTOS):
    return pd.DataFrame({
        "id": [f"__export__.product_template_{i}_ab12cd34" for i in range(len(productos))],
        "name": [p[1] for p in productos],
        "default_code": [p[0] for p in productos],
        "standard_price": [str(p[2]) for p in productos],
        "is_published": ["True"] * len(productos),
        "qty_available": ["7"] * len(productos),
    })


@pytest.fixture
def catalogo():
    return construir(export_df())


@pytest.fixture
def memoria(tmp_path):
    return Memoria(tmp_path / "memoria")


@pytest.fixture
def ciclo(catalogo, memoria):
    """ciclo(texto) corre un ciclo completo sobre el catálogo de prueba."""
    def _run(texto, **kw):
        return correr(catalogo, memoria, texto, **kw)
    return _run
