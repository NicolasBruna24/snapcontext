def todos_positivos(numeros):
    for n in numeros:
        if n < 0:
            return False
    return True
