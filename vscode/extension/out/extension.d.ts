/**
 * SnapContext — Extensión para VS Code en TypeScript (v3.2.0)
 *
 * Reutiliza la CLI/módulo de SnapContext (`python -m snapcontext`) y la
 * interfaz web existente (`web/app.py`) dentro de VS Code:
 *
 *   - Canal de salida "SnapContext Output" con los logs del orquestador.
 *   - "Abrir chat": arranca el servidor web de SnapContext y lo muestra en
 *     una webview (http://localhost:<puerto dinámico>).
 *   - "Ejecutar consulta" / "Planificar": ejecutan la CLI con el workspace
 *     abierto como --directorio y muestran el progreso en el canal de salida.
 *   - "Añadir al contexto": selección visual de archivos del explorador que
 *     se adjuntan como contexto a las consultas (equivalente visual a /add).
 */
import * as vscode from "vscode";
export declare function activate(context: vscode.ExtensionContext): void;
export declare function deactivate(): void;
/** SnapContext: Abrir chat — webview con la interfaz web existente. */
export declare function abrirChat(): Promise<void>;
/** SnapContext: Ejecutar consulta. */
export declare function ejecutarConsulta(): Promise<void>;
/** SnapContext: Planificar (--plan). */
export declare function planificar(): Promise<void>;
/** SnapContext: Configurar API key (persistida en settings del workspace). */
export declare function configurarApiKey(): Promise<void>;
/** Añade archivos del explorador al contexto visual (tipo /add de Aider). */
export declare function anadirAlContexto(uri?: vscode.Uri): void;
/** Vacía la selección de contexto visual. */
export declare function limpiarSeleccion(): void;
