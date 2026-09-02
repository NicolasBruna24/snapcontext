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
import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import * as path from "path";
import * as net from "net";

/** Versión de la extensión (se muestra en el canal de salida). */
const VERSION = "6.15.0";

/** Canal de salida compartido ("SnapContext Output"). */
let salida: vscode.OutputChannel | null = null;
/** Barra de estado con la actividad actual. */
let estadoBarra: vscode.StatusBarItem | null = null;
/** Archivos seleccionados desde el explorador (contexto visual tipo /add). */
const archivosContexto = new Set<string>();
/** Proceso del servidor web del chat, si está activo. */
let procesoChat: ChildProcessWithoutNullStreams | null = null;

/** Canal de salida garantizado (lanza si la extensión no está activa). */
function canal(): vscode.OutputChannel {
  if (!salida) {
    throw new Error("SnapContext: la extensión no está activada.");
  }
  return salida;
}

/** Barra de estado garantizada. */
function barra(): vscode.StatusBarItem {
  if (!estadoBarra) {
    throw new Error("SnapContext: la extensión no está activada.");
  }
  return estadoBarra;
}

export function activate(context: vscode.ExtensionContext): void {
  salida = vscode.window.createOutputChannel("SnapContext Output");
  context.subscriptions.push(salida);

  estadoBarra = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left, 10);
  estadoBarra.text = "$(sparkle) SnapContext";
  estadoBarra.command = "snapcontext.ejecutarConsulta";
  estadoBarra.tooltip = "SnapContext: clic para ejecutar una consulta";
  estadoBarra.show();
  context.subscriptions.push(estadoBarra);

  const registros: Array<[string, (...args: unknown[]) => unknown]> = [
    ["snapcontext.abrirChat", () => abrirChat()],
    ["snapcontext.ejecutarConsulta", () => ejecutarConsulta()],
    ["snapcontext.planificar", () => planificar()],
    ["snapcontext.configurarApiKey", () => configurarApiKey()],
    ["snapcontext.anadirAlContexto",
      (uri: unknown) => anadirAlContexto(uri as vscode.Uri)],
    ["snapcontext.limpiarSeleccion", () => limpiarSeleccion()],
  ];
  for (const [comando, callback] of registros) {
    const disposable: vscode.Disposable =
      vscode.commands.registerCommand(comando, callback);
    context.subscriptions.push(disposable);
  }

  // v6.15.0: vista "SnapContext: Chat" en la Activity Bar. Reutiliza la
  // misma lógica de abrirChat() pero dentro del panel lateral.
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      "snapcontext.chat",
      new ChatViewProvider(),
      { webviewOptions: { retainContextWhenHidden: true } }));

  canal().appendLine(`SnapContext v${VERSION} listo.`);
}

export function deactivate(): void {
  if (procesoChat) {
    procesoChat.kill();
    procesoChat = null;
  }
}

// ---------------------------------------------------------------------------
// v6.15.0: proveedor de la vista lateral "SnapContext: Chat"
// ---------------------------------------------------------------------------
/**
 * Resuelve la vista `snapcontext.chat` de la Activity Bar: arranca el
 * servidor web de SnapContext (si no está ya activo) y muestra la interfaz
 * en un iframe dentro del panel lateral.
 */
class ChatViewProvider implements vscode.WebviewViewProvider {
  /** URL del servidor del chat ya arrancado (compartida entre vistas). */
  private static urlActiva: string | null = null;

  resolveWebviewView(
    vista: vscode.WebviewView,
    _contexto: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    vista.webview.options = { enableScripts: true };
    vista.webview.html = htmlWebview(CargandoView.HTML);

    void (async () => {
      try {
        if (!ChatViewProvider.urlActiva) {
          ChatViewProvider.urlActiva = await arrancarServidorChat();
        }
        vista.webview.html = htmlWebview(ChatViewProvider.urlActiva);
      } catch (err) {
        vista.webview.html = htmlWebview(
          ErrorView.HTML
          + (err instanceof Error ? err.message : String(err))
          + ErrorView.PIE);
      }
    })();
  }
}

/** Pantalla de carga mientras arranca el servidor del chat. */
const CargandoView = {
  HTML: `<p style="font-family:sans-serif;color:#ccc;padding:12px">
    ⏳ Arrancando el chat de SnapContext…</p>`,
};

/** Pantalla de error si el servidor no pudo arrancar. */
const ErrorView = {
  HTML: `<p style="font-family:sans-serif;color:#f66;padding:12px">
    ✖ No se pudo iniciar el chat de SnapContext.</p>
    <pre style="color:#ccc;padding:0 12px;white-space:pre-wrap">`,
  PIE: `</pre>`,
};

// ---------------------------------------------------------------------------
// Comandos
// ---------------------------------------------------------------------------
/** SnapContext: Abrir chat — webview con la interfaz web existente. */
export async function abrirChat(): Promise<void> {
  const url = await arrancarServidorChat();
  canal().show(true);
  canal().appendLine(`💬 Chat web activo en ${url}`);

  const panel: vscode.WebviewPanel = vscode.window.createWebviewPanel(
    "snapcontextChat",
    "SnapContext Chat",
    vscode.ViewColumn.One,
    { enableScripts: true },
  );
  panel.webview.html = htmlWebview(url);
  panel.onDidDispose(() => {
    if (procesoChat) { procesoChat.kill(); procesoChat = null; }
  });
}

/**
 * Arranca el servidor web de SnapContext en un puerto libre (lazy: solo al
 * abrir el chat o la vista lateral). Devuelve `http://localhost:<puerto>`.
 * Lanza Error si el servidor no respondió antes del timeout.
 */
async function arrancarServidorChat(): Promise<string> {
  let puerto = 8765;
  while (!(await libre(puerto))) {
    puerto += 1;
  }

  const pythonPath = config().get<string>("pythonPath", "python");
  const ws = workspaceActual();
  const script = (
    "from web.app import arrancar_servidor; import threading, time;"
    + "threading.Thread(target=arrancar_servidor,"
    + `kwargs={'puerto': ${puerto}}, daemon=True).start();`
    + "time.sleep(36000)");
  // v6.15.0: shell:false para que el script `-c` llegue intacto a Python
  // (con shell:true, cmd.exe rompía las comillas del kwargs del script).
  procesoChat = spawn(pythonPath, ["-c", script], {
    cwd: ws,
    env: entornoConClave(),
    shell: false,
  });
  procesoChat.stdout?.on("data", (b: Buffer) => canal().appendLine("[chat] " + b));
  procesoChat.stderr?.on("data", (b: Buffer) => canal().appendLine("[chat] ⚠ " + b));
  procesoChat.on("close", () => { procesoChat = null; });

  const listo = await esperarPuerto(puerto, 10000);
  if (!listo) {
    throw new Error("El servidor del chat no respondió en 10 s "
      + "(revisa \"snapcontext.pythonPath\" y que snapcontext esté instalado).");
  }
  return `http://localhost:${puerto}`;
}

/** SnapContext: Ejecutar consulta. */
export async function ejecutarConsulta(): Promise<void> {
  const consulta = await vscode.window.showInputBox({
    prompt: "SnapContext: ¿qué quieres hacer?",
    placeHolder: "p. ej.: arregla el botón de pago",
    ignoreFocusOut: true,
  });
  if (!consulta) { return; }
  const args = argumentosBase().concat([consulta]).concat(sufijoContexto());
  await ejecutarSnap(args, "Ejecutar consulta");
}

/** SnapContext: Planificar (--plan). */
export async function planificar(): Promise<void> {
  const consulta = await vscode.window.showInputBox({
    prompt: "SnapContext: describe la tarea a planificar",
    placeHolder: "p. ej.: migrar el checkout a la nueva API",
    ignoreFocusOut: true,
  });
  if (!consulta) { return; }
  const args = argumentosBase()
    .concat(["--plan", consulta, "--no-confirmar"])
    .concat(sufijoContexto());
  await ejecutarSnap(args, "Planificador (--plan)");
}

/** SnapContext: Configurar API key (persistida en settings del workspace). */
export async function configurarApiKey(): Promise<void> {
  const clave = await vscode.window.showInputBox({
    prompt: "Clave API del proveedor configurado (se guarda en settings)",
    password: true,
    ignoreFocusOut: true,
  });
  if (!clave) { return; }
  await config().update("apiKey", clave,
    vscode.ConfigurationTarget.Workspace);
  vscode.window.showInformationMessage(
    "SnapContext: API key guardada en la configuración del workspace.");
}

/** Añade archivos del explorador al contexto visual (tipo /add de Aider). */
export function anadirAlContexto(uri?: vscode.Uri): void {
  if (!uri || !uri.fsPath) { return; }
  try {
    const relativo = path.relative(workspaceActual(), uri.fsPath)
      .replace(/\\/g, "/");
    archivosContexto.add(relativo);
    vscode.commands.executeCommand("setContext",
      "snapcontext.tieneSeleccion", archivosContexto.size > 0);
    vscode.window.showInformationMessage(
      `SnapContext: ${archivosContexto.size} archivo(s) en contexto.`);
    canal().appendLine(`📎 Contexto añadido: ${relativo}`);
  } catch (err) {
    vscode.window.showErrorMessage(
      err instanceof Error ? err.message : String(err));
  }
}

/** Vacía la selección de contexto visual. */
export function limpiarSeleccion(): void {
  archivosContexto.clear();
  vscode.commands.executeCommand("setContext",
                                 "snapcontext.tieneSeleccion", false);
  vscode.window.showInformationMessage(
    "SnapContext: contexto de archivos vaciado.");
}

// ---------------------------------------------------------------------------
// Ayudas del chat webview
// ---------------------------------------------------------------------------
/** True si un puerto TCP está libre en localhost. */
function libre(puerto: number): Promise<boolean> {
  return new Promise((resolver: (libre: boolean) => void) => {
    const servidor = net.createServer();
    servidor.once("error", () => resolver(false));
    servidor.once("listening", () => {
      servidor.close(() => resolver(true));
    });
    servidor.listen(puerto, "127.0.0.1");
  });
}

/** Espera (con timeout) a que un puerto empiece a aceptar conexiones. */
function esperarPuerto(puerto: number, timeoutMs: number): Promise<boolean> {
  const inicio = Date.now();
  function intentar(resolver: (ok: boolean) => void): void {
    const cliente = net.connect(puerto, "127.0.0.1");
    cliente.once("connect", () => { cliente.end(); resolver(true); });
    cliente.once("error", () => {
      cliente.destroy();
      if (Date.now() - inicio > timeoutMs) {
        resolver(false);
      } else {
        setTimeout(() => intentar(resolver), 300);
      }
    });
  }
  return new Promise(intentar);
}

/** HTML mínimo de la webview: iframe a la interfaz web existente. */
function htmlWebview(url: string): string {
  return `<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8">
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #1e1e2e; }
  iframe { border: 0; width: 100%; height: 100vh; display: block; }
</style></head>
<body>
  <iframe src="${url}" allow="clipboard-read; clipboard-write"></iframe>
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------
/** Ruta del primer workspace abierto (error claro si no hay ninguno). */
function workspaceActual(): string {
  const carpetas = vscode.workspace.workspaceFolders;
  if (!carpetas || carpetas.length === 0) {
    throw new Error("Abre una carpeta en VS Code para usar SnapContext.");
  }
  return carpetas[0].uri.fsPath;
}

/** Sección "snapcontext" de la configuración. */
function config(): vscode.WorkspaceConfiguration {
  return vscode.workspace.getConfiguration("snapcontext");
}

/** Argumentos base comunes: proveedor y confirmaciones. */
function argumentosBase(): string[] {
  const args: string[] = [];
  const proveedor = config().get<string | undefined>("provider");
  if (proveedor) {
    args.push("--provider", String(proveedor));
  }
  if (!config().get<boolean>("confirmar", true)) {
    args.push("--no-confirmar");
  }
  return args;
}

/** Contexto visual: archivos marcados en el explorador. */
function sufijoContexto(): string[] {
  if (archivosContexto.size === 0) {
    return [];
  }
  return ["(Revisa especialmente estos archivos: "
          + [...archivosContexto].join(", ") + ")"];
}

/** Variables de entorno con la API key configurada (si la hay). */
function entornoConClave(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = Object.assign({}, process.env);
  const claveApi = config().get<string>("apiKey", "");
  if (claveApi) {
    env.GEMINI_API_KEY = claveApi;
    env.ANTHROPIC_API_KEY = claveApi;
    env.DEEPSEEK_API_KEY = claveApi;
    env.GROQ_API_KEY = claveApi;
  }
  return env;
}

/**
 * Ejecuta `python -m snapcontext <args>` en el workspace y vuelca stdout/
 * stderr en el canal de salida. Devuelve el código de salida vía promesa.
 */
function ejecutarSnap(args: string[], titulo: string): Promise<number> {
  return new Promise((resolve: (codigo: number) => void) => {
    let ws: string;
    try {
      ws = workspaceActual();
    } catch (err) {
      vscode.window.showErrorMessage(
        err instanceof Error ? err.message : String(err));
      resolve(1);
      return;
    }
    const pythonPath = config().get<string>("pythonPath", "python");
    const argv: string[] = ["-m", "snapcontext"].concat(args);
    const canalSalida = canal();

    canalSalida.show(true);
    canalSalida.appendLine("");
    canalSalida.appendLine("─".repeat(60));
    canalSalida.appendLine(`▶ ${titulo}`);
    canalSalida.appendLine(`  $ ${path.basename(pythonPath)} ${argv.join(" ")}`);
    canalSalida.appendLine(`  📁 ${ws}`);

    barra().text = "$(sync~spin) SnapContext: trabajando…";
    const inicio = Date.now();
    // v6.15.0: sin `shell: true`. Con shell, cmd.exe interpreta caracteres
    // especiales de la consulta (paréntesis, &, comillas…) y provoca
    // `SyntaxError: invalid syntax` en Python. Con shell:false el array de
    // argumentos se pasa intacto al proceso (Node hace el escaping correcto).
    const proc: ChildProcessWithoutNullStreams =
      spawn(pythonPath, argv, {
        cwd: ws,
        env: entornoConClave(),
        shell: false,
        windowsVerbatimArguments: false,
      });

    const pintar = (buf: Buffer, esError: boolean = false): void => {
      for (const linea of buf.toString().split(/\r?\n/)) {
        if (linea.trim()) {
          canalSalida.appendLine((esError ? "⚠ " : "  ") + linea);
        }
      }
    };
    proc.stdout.on("data", (b: Buffer) => pintar(b));
    proc.stderr.on("data", (b: Buffer) => pintar(b, true));

    proc.on("close", (codigo: number | null) => {
      const segundos = ((Date.now() - inicio) / 1000).toFixed(1);
      canalSalida.appendLine(`${codigo === 0 ? "✔" : "✖"} Terminado `
                            + `(código ${codigo}, ${segundos}s)`);
      barra().text = codigo === 0
        ? "$(check) SnapContext"
        : "$(error) SnapContext";
      resolve(codigo === 0 ? 0 : (codigo ?? 1));
    });
    proc.on("error", (err: Error) => {
      canalSalida.appendLine(`✖ No se pudo lanzar Python (${pythonPath}): `
                            + err.message);
      vscode.window.showErrorMessage(
        "SnapContext: no se pudo lanzar Python. Revisa "
        + "\"snapcontext.pythonPath\" y que snapcontext esté instalado "
        + "(pip install snapcontext).");
      barra().text = "$(error) SnapContext";
      resolve(1);
    });
  });
}
