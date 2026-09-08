# 🎯 Perfiles de prompt optimizados por modelo (Fase 17)

## Qué es

`prompt_profiles.py` selecciona un **prompt y parámetros específicos** según el
proveedor/modelo en uso y el tipo de tarea (simple, planificación, edición...).
Es **solo generación de prompt**: nunca cambia la lógica de los proveedores.

Cada perfil tiene:

| Campo | Descripción |
|-------|-------------|
| `system_prompt` | Instrucciones de sistema específicas del modelo |
| `user_prompt_template` | Plantilla del mensaje de usuario (`{consulta}`, `{contexto}`) |
| `config` | Parámetros (`temperature`, `max_tokens`, `cache_control`) |
| `variantes` | Overrides del `system_prompt` por tipo de tarea |

## Perfiles incluidos

| Clave | Enfoque |
|-------|---------|
| `claude` (alias `anthropic`) | Razonamiento profundo paso a paso; `cache_control: True` (v6.34+) |
| `gemini` | Instrucciones claras y directas, sin complejidad |
| `openai` | Precisión y concisión (plantilla en inglés) |
| `deepseek` | Preciso y eficiente; caching activado |
| `ollama` (alias `local`) | Contexto e instrucciones mínimos para evitar divagaciones |
| `xpu` | Respuestas breves; contexto muy reducido (hardware local) |
| `groq` | Directo, código correcto sin rodeos |

Cualquier proveedor no registrado recibe `PERFIL_GENERICO` (fallback, nunca
rompe el flujo).

## Cómo se aplica

1. `_enviar_al_proveedor` determina el `tipo_tarea` con `_tipo_tarea_actual()`
   (modo detectado: `--plan` → `planificacion`; chat/ReAct → `simple`).
2. `_enviar_al_proveedor_unico` llama a `prompt_profiles.aplicar_perfil()`,
   que inyecta el `system_prompt` y rellena la plantilla del usuario.
3. Cada API recibe el system en su formato nativo: OpenAI/Groq/Ollama como
   mensaje `system`, Anthropic como parámetro `system=`, Gemini como
   `system_instruction=`.
4. Para tareas **estructuradas** (plan JSON, edición, QA, tests) NO se
   reformatea el mensaje del usuario, para no romper el parser posterior.
5. Si algo falla, se envían los mensajes originales (try/except silencioso).

## Personalización (sin tocar el código)

Añade la clave `prompt_profiles` a `~/.snapcontext/config.json`. Se hace un
merge profundo sobre los perfiles por defecto, así que puedes sobreescribir
solo un campo o definir un proveedor nuevo completo:

```json
{
  "prompt_profiles": {
    "claude": { "config": { "temperature": 0.0 } },
    "mistral": {
      "system_prompt": "Sé conciso.",
      "user_prompt_template": "Pregunta: {consulta}\n\nContexto: {contexto}",
      "config": { "temperature": 0.5, "max_tokens": 1000, "cache_control": false },
      "variantes": {}
    }
  }
}
```

## Tests

`tests/test_prompt_profiles.py` (perfiles, variantes, fallback, merge de
config) y `tests/test_prompt_profiles_integration.py` (inyección del
`system_prompt` en los SDK de OpenAI, Anthropic y Gemini con mocks, sin
llamadas a APIs reales).
