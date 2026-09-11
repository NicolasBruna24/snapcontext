import logging
logging.basicConfig(level=logging.INFO)

def procesar(datos):
    logging.info('Procesando %d elementos', len(datos))
    return [x * 2 for x in datos]
