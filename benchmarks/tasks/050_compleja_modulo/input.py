def procesar_archivo(ruta):
    with open(ruta) as f:
        datos = f.read()
    lineas = datos.split('\n')
    return [l.strip() for l in lineas if l]
