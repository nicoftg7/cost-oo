# Actualizador de costos: cómo usarlo

Los proveedores mandan precios por WhatsApp, planillas o listas. Esta app los lee, encuentra
cada producto en Odoo y arma un archivo para importar los costos nuevos. **Solo toca el costo**:
nunca el precio de venta, el stock ni la publicación.

## Descargarla

1. Entrá a https://github.com/nicoftg7/cost-oo, tocá el botón verde **Code** y después
   **Download ZIP**.
2. En la carpeta de Descargas, clic derecho sobre el ZIP → **Extraer todo**, y elegí dónde
   dejar la carpeta (por ejemplo, en Documentos). **No abras la app desde adentro del ZIP**:
   Windows la correría desde una carpeta temporal y se perdería todo lo que aprende.

Funciona en Windows con Python 3.11 o más nuevo (la última versión de python.org sirve).

## Para probarla sin tocar nada real

Doble clic en **Probar con el ejemplo**: abre la app con un almacén inventado (otra carpeta de
datos, otro puerto). Tocá *Cargar el ejemplo y analizar*, respondé lo que pregunta y volvé a analizar.

## La primera vez

1. Si la computadora no tiene Python, instalalo desde https://www.python.org/downloads/
   y **tildá "Add python.exe to PATH"** al instalar.
2. Doble clic en **Abrir actualizador**. La primera vez tarda un par de minutos en prepararse.
   Se abre una ventana negra (no la cierres mientras usás la app) y el navegador con la app.
3. La planilla de mensajes de Google Sheets tiene que estar compartida como
   **"Cualquier persona con el enlace: Lector"**. El link se pega una sola vez.

## Cada vez que actualizás costos

1. **Exportá los productos de Odoo**: Productos → vista lista → seleccionar todos →
   Acción → Exportar → tildá **"Quiero actualizar datos (exportación compatible con importación)"**
   → campos: Nombre, Referencia interna, Costo, Precio de venta, Publicado, Cantidad a la mano → Exportar.
   Cargalo en el paso 1.
2. **Sumá lo que mandaron los proveedores** (se puede combinar):
   - **Planilla de Google Sheets**: un clic. Los mensajes que ya se procesaron se saltean solos.
     La app también avisa qué proveedores de la pestaña *Lista* todavía no cargaste, y recuerda
     los de la pestaña *Manual*.
   - **Listas de precios** en PDF o Excel: arrastrás una o varias y elegís de qué proveedor es cada
     una. La primera vez pregunta si la lista incluye IVA; después ya lo sabe. Si en Odoo
     cargás el costo sin IVA, decilo en *Lo aprendido → datos del negocio*: a las listas con IVA
     se les saca, en vez de sumárselo a las que vienen sin.
   - **Mensaje suelto**: elegís el proveedor y pegás el mensaje.
   - Si un proveedor no está en la lista para elegir, escribí su nombre: queda guardado.
3. **Analizá y confirmá lo amarillo.** Son los casos en que la app no está segura.
4. **Mirá la lista final.** Lo raro aparece arriba, en amarillo: aumentos grandes, costos por encima
   del precio de venta, etc. Cada costo dice de qué mensaje o lista salió.
5. **Generá el archivo e importalo en Odoo** (Productos → ⚙ → Importar registros).
6. **Verificá**: exportá otra vez de Odoo y subilo en la última pantalla. La app confirma que cada
   costo haya quedado bien y que no se hayan creado productos repetidos.

Adentro de la app, en **Cómo funciona**, está todo explicado con más detalle.

## Lo importante

- Cada confirmación queda guardada: **el mes siguiente la app pregunta menos**. En las listas de
  distribuidoras aprende el código de cada artículo, y desde la segunda vez entran solos.
- Si un producto lo venden varios proveedores, la app sabe a quién se lo comprás y **pregunta
  antes de usar el precio de otro**.
- Si confirmaste algo mal, entrá a **Lo aprendido**, borrá esa fila y volvé a analizar.
- Si el export de Odoo no tiene la columna `id`, la app no sigue. Es a propósito: importar sin
  esa columna **crea productos duplicados** en vez de actualizar.
- La carpeta `datos` guarda lo aprendido, el historial y los archivos generados. **No la borres.**
  Para pasarle la app a otra computadora, copiá la carpeta entera.
