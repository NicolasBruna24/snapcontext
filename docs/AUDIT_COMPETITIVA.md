# Auditoría de brechas competitivas — SnapContext vs. mercado

*Fecha: 2026-08-09 · Baseline: post-Fase 18 (commit `983813a`, v6.34.12)*

## 1. Resumen ejecutivo

SnapContext es hoy un asistente agéntico **técnicamente sólido y honesto**:
multi-proveedor (con perfiles por modelo y XPU/Battlemage único en el mercado),
Graph RAG con indexación en background, LSP con degradación elegante, permisos,
sandbox y sistema de hooks/plugins. Sin embargo, **pierde claramente en
ecosistema y distribución**: no hay integración nativa con IDEs (VSCode/JetBrains),
no existe el paquete publicado en PyPI con adopción real, la cobertura de tests
del monolito es baja (~9-20%) y el onboarding depende de un `--init` propio
frente al zero-config de Claude Code/Cline. El monolito de ~12.800 líneas en
`snapcontext.py` sigue siendo el mayor riesgo para la velocidad de desarrollo.

## 2. Tabla comparativa

Leyenda: ✅ sólido · 🟡 parcial/honesto · ❌ ausente

| Área | SnapContext | Claude Code | Aider | Cline | OpenCode | Hermes |
|---|---|---|---|---|---|---|
| **Onboarding** | 🟡 `pip install` + `--init` con guía de proveedores | ✅ `npm i -g` + OAuth, listo en 1 min | ✅ pip + API key | ✅ extensión VSCode, sin CLI | ✅ binario único | 🟡 |
| **UX (TUI/CLI/IDE)** | 🟡 CLI + TUI textual propia | ✅ terminal pulida + integración IDE | 🟡 CLI sencillo, diff en vivo | ✅ nativo en VSCode | ✅ TUI moderna (Go) | 🟡 |
| **Capacidad agéntica** | ✅ ReAct + planificador + multi-agente + sub-agentes paralelos | ✅ (sin sub-agentes formales) | 🟡 bucle sencillo de edición | ✅ plan/act | ✅ agente + LSP | 🟡 |
| **Edición de código** | 🟡 motor propio + autocorrector; sin benchmark público | ✅ edit-tool muy fiable | ✅ pionero del diff search/replace | ✅ | 🟡 | 🟡 |
| **Ejecución / sandbox** | ✅ sandbox Docker, permisos, `shell=False` | 🟡 permisos, sin Docker | 🟡 confirmación | ✅ auto-aprobación configurable | 🟡 | 🟡 |
| **RAG y contexto** | ✅ Graph RAG + LSP + embeddings ligeros + indexación en background | ❌ (grep + archivos) | 🟡 repo-map | 🟡 | 🟡 LSP | ❌ |
| **Multi-proveedor** | ✅ 7+ proveedores + perfiles por modelo + Ollama/XPU local | ❌ solo Anthropic | 🟡 | 🟡 | ✅ multi | ✅ |
| **MCP** | 🟡 herramientas MCP propias (db/api/browser); cliente estándar sin verificar | ✅ cliente+servidor | ❌ | ✅ cliente de facto | ✅ | 🟡 |
## 3. Brechas identificadas (top 5)

1. **Distribución y cero installed-base**: no hay evidencia de publicación
   estable en PyPI ni de adopción. Claude Code, Aider, Cline y OpenCode ganan
   por ecosistema, no por arquitectura. Sin usuarios, marketplace y hooks
   son islas.
2. **Integración con IDE**: Cline y Claude Code viven donde el desarrollador
   ya está (VSCode). La TUI propia no sustituye ese canal. Falta una extensión
   VSCode (aunque sea cliente del gateway web ya existente).
3. **Fiabilidad de edición demostrable**: Aider y Claude Code publican
   resultados (SWE-bench, benchmarks de edit-format). SnapContext no tiene un
   número que defender; sin benchmark, "fiable" es una afirmación, no un dato.
4. **Cobertura de tests del monolito (~9-20%)**: el flujo crítico
   (`flujo_principal` → Orquestador → `_enviar_al_proveedor`) está poco
   cubierto; las fases 16-18 testearon los módulos nuevos, no el núcleo.
   `ruff`/`mypy` no corren en CI (ruff ni siquiera está instalado en el
   entorno de desarrollo actual).
5. **Cliente MCP estándar + documentación de referencia**: se exponen
   herramientas MCP propias, pero la conectividad como *cliente* a servidores
   MCP de terceros (lo que hace útiles a Cline/OpenCode desde el día 1) no
   está documentada ni verificada. Falta también `docs/CLI.md` con todas las
   flags y ejemplos.

## 4. Ventajas únicas (top 5)

1. **Graph RAG con indexación en background + degradación elegante a regex/LSP**
   (Fase 16): nadie en la tabla tiene esto; Claude Code y Aider usan grep.
2. **Perfiles de prompt por modelo** (Fase 17), personalizables vía
   `config.json` sin tocar código — los competidores usan prompts genéricos.
3. **Intel XPU/Battlemage con IPEX-LLM low-bit** (Fase 18): soporte de primera
   clase para Arc B70 que ningún competidor ofrece; nicho defendible.
4. **Seguridad como ciudadano de primera clase**: permisos persistidos,
   sandbox Docker, validación de rutas — por encima de Aider/OpenCode.
5. **Multi-agente y paralelización nativa** (`sub_agent`, `parallel_executor`,
   `model_router`, `prompt_cache`, `context_pruner`) — arquitectura más
   ambiciosa que Aider o Cline.

## 5. Plan de acción recomendado (priorizado)

1. **Publicar en PyPI y medir adopción** (semana 1-2): paquete instalable con
   `pipx`, GitHub Actions que publique en cada tag, badge de versión. Sin
   esto, todo lo demás no llega a nadie.
2. **Benchmark de edición propio y público** (semana 2-3): ~50 tareas de
   edición reproducibles (estilo Aider benchmark) con resultados en el README.
   Convierte el motor de edición de "creemos que funciona" a "funciona X%".
3. **Extensión VSCode mínima** (semana 3-6): cliente del gateway web existente
   (panel lateral + diff + aprobaciones). Es la brecha de distribución más
   dolorosa frente a Cline.
4. **Subir cobertura del núcleo al 60%** (continuo): `flujo_principal`,
   Orquestador, `_enviar_al_proveedor`, motor de edición. Activar `ruff` +
   `mypy` en CI con umbral duro (Fase 10c pendiente).
5. **Cliente MCP estándar + docs de referencia** (semana 4): conectar a
   servidores MCP de terceros y crear `docs/CLI.md` con todas las flags y
   ejemplos.

## 6. Conclusión

**¿Estamos listos para competir?** Arquitectónicamente sí: hay capacidades
(Graph RAG, perfiles, XPU, seguridad) mejores que las de los líderes.
**Competitivamente, todavía no**: ganan por distribución (IDE + PyPI +
comunidad), no por tecnología. La venta de SnapContext no puede ser "tenemos
más fases", sino "índice tu repo en segundos y funciona con tu GPU Intel":
eso hay que empaquetarlo y mostrarlo.

**¿Qué falta para ser líderes indiscutibles?** Tres cosas: **distribución**
(PyPI + extensión VSCode), **prueba objetiva** (benchmark de edición y
cobertura del núcleo) y **comunidad** (usuarios reales usando hooks y
marketplace). La tecnología ya está; el producto y el canal, no.

| **Seguridad** | ✅ validación rutas, permisos persistidos, auditoría | ✅ | 🟡 | ✅ | 🟡 | 🟡 |
| **Calidad del código** | 🟡 91 archivos de test, cobertura baja; ruff/mypy no bloquean CI | ✅ | ✅ | ✅ | ✅ | 🟡 |
| **Documentación** | 🟡 README + 6 docs de fase; faltan tutoriales y referencia CLI completa | ✅ sobresaliente | ✅ | ✅ | ✅ | 🟡 |
| **Ecosistema/plugins** | 🟡 hooks, marketplace, skill abstraction — sin comunidad que los use | ✅ hooks, SDK | 🟡 | ✅ marketplace activo | 🟡 | 🟡 |
