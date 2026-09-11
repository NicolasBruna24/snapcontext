def leer_archivo(ruta):
    with open(ruta) as f:
        return f.read()

def procesar_datos(datos):
    lineas = datos.split('\n')
    return [l.strip() for l in lineas if l]

def procesar_archivo(ruta):
    datos = leer_archivo(ruta)
    return procesar_datos(datos)
