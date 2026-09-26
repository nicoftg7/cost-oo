"""Motor del actualizador de costos. No depende de ninguna interfaz."""


class ErrorDeDatos(Exception):
    """Un archivo de entrada no sirve y seguir sería peligroso.

    El mensaje está escrito para la persona que opera la app, no para un programador:
    tiene que decir qué pasó y qué hacer.
    """
