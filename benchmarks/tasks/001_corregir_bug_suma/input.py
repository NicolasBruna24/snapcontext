def suma(a, b):
    """Suma dos números. Bug: falla con negativos."""
    return a - b  # Bug: debería ser a + b


if __name__ == "__main__":
    print(suma(2, 3))      # Esperado: 5
    print(suma(-1, 5))     # Esperado: 4
    print(suma(-3, -7))    # Esperado: -10
