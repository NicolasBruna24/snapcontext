from pathlib import Path

def leer_archivo(nombre):
    ruta = Path('data') / nombre
    return ruta.read_text()
