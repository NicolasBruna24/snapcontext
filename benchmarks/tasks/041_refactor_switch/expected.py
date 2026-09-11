def obtener_letra(nota):
    mapping = {'A': 'Excelente', 'B': 'Bueno', 'C': 'Regular'}
    return mapping.get(nota, 'Desconocido')
