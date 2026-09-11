def leer(ruta):
    f = open(ruta)
    datos = f.read()
    f.close()
    return datos
