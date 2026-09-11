import os

def leer_archivo(nombre):
    ruta = os.path.join('data', nombre)
    return open(ruta).read()
