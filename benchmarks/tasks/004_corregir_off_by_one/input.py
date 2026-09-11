def sumar_hasta(n):
    total = 0
    for i in range(n):  # Bug: range(n) excluye n
        total += i
    return total
