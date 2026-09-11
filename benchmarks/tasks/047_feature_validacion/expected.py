def suma(lista):
    if not isinstance(lista, (list, tuple)):
        raise TypeError('Se espera lista o tupla')
    return sum(lista)
