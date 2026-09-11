def parsear(texto):
    try:
        return int(texto)
    except ValueError:
        return None
