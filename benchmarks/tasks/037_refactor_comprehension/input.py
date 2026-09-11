def pares(numeros):
    result = []
    for n in numeros:
        if n % 2 == 0:
            result.append(n)
    return result
