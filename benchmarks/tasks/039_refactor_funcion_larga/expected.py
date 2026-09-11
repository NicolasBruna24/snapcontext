def validar_usuario(nombre, edad, email):
    if not nombre or len(nombre) < 2:
        raise ValueError('nombre inválido')
    if edad < 0 or edad > 150:
        raise ValueError('edad inválida')
    if '@' not in email:
        raise ValueError('email inválido')

def procesar_usuario(nombre, edad, email):
    validar_usuario(nombre, edad, email)
    return {'nombre': nombre, 'edad': edad, 'email': email}
