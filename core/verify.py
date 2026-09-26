"""El doble chequeo después de importar: se exporta de nuevo de Odoo y se compara.

Responde tres preguntas:
  1. ¿Cada producto del archivo quedó con el costo que tenía que quedar?
  2. ¿Odoo creó productos nuevos? (la señal de que el import duplicó en vez de actualizar)
  3. ¿Cambió el costo de algo que NO estaba en el archivo?
"""
import pandas as pd

TOLERANCIA = 0.01


def verificar(esperado, antes, despues):
    """esperado: tabla del import (id, default_code, name, standard_price).
    antes / despues: catálogos (core.catalog.construir) del export previo y del nuevo."""
    d = despues.drop_duplicates("id_externo").set_index("id_externo")
    a = antes.drop_duplicates("id_externo").set_index("id_externo")

    filas = []
    for r in esperado.to_dict("records"):
        i = r["id"]
        quiero = float(r["standard_price"])
        if i not in d.index:
            filas.append({**r, "costo_en_odoo": None, "estado": "no aparece en el export nuevo"})
            continue
        hay = float(d.loc[i, "costo_actual"])
        estado = "ok" if abs(hay - quiero) <= TOLERANCIA else "quedó distinto"
        if estado != "ok" and i in a.index and abs(float(a.loc[i, "costo_actual"]) - hay) <= TOLERANCIA:
            estado = "no se actualizó"
        filas.append({**r, "costo_en_odoo": hay, "estado": estado,
                      "nombre_en_odoo": d.loc[i, "nombre_completo"]})
    control = pd.DataFrame(filas)

    nuevos = despues[~despues.id_externo.isin(a.index)]
    # también por nombre: un duplicado tiene el mismo nombre que uno que ya existía
    duplicados = despues[despues.duplicated("nombre_completo", keep=False)
                         & despues.id_externo.isin(nuevos.id_externo)]

    en_archivo = set(esperado["id"])
    comunes = a.index.intersection(d.index).difference(list(en_archivo))
    cambios = [{"id": i, "producto": d.loc[i, "nombre_completo"], "antes": float(a.loc[i, "costo_actual"]),
                "ahora": float(d.loc[i, "costo_actual"])}
               for i in comunes
               if abs(float(a.loc[i, "costo_actual"]) - float(d.loc[i, "costo_actual"])) > TOLERANCIA]

    ok = int((control["estado"] == "ok").sum()) if len(control) else 0
    return {"control": control, "ok": ok, "total": len(control),
            "problemas": control[control["estado"] != "ok"] if len(control) else control,
            "productos_antes": len(antes), "productos_despues": len(despues),
            "nuevos": nuevos, "duplicados": duplicados, "otros_cambios": pd.DataFrame(cambios),
            "todo_bien": len(control) > 0 and ok == len(control) and not len(nuevos)}
