def eliminar_elemento(lista, elemento):
    lista.remove(elemento)  # Bug: modifica original
    return lista
