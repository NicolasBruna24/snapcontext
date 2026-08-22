/**
 * SnapContext — Extensión para VS Code (v0.16.0)
 *
 * Reutiliza la CLI/módulo de SnapContext (`python -m snapcontext`) y la
 * interfaz web existente (`web/app.py`) dentro de VS Code:
 *
 *   - Canal de salida "SnapContext Output" con los logs del orquestador.
 *   - "Abrir chat": arranca el servidor web de SnapContext y lo muestra en
 *     una webview.
 *   - "Ejecutar consulta" / "Planificar": ejecutan la CLI con el workspace
 *     abierto como --directorio y muestran el progreso en el canal de salida.
 *   - "Añadir al contexto": selección visual de archivos del explorador que
 *     se adjuntan como contexto a las consultas (equivalente visual a /add).
 */

const vscode = require("vscode");
const { spawn } = require("child_process");
const path = require("path");
const net = require("net");

/** Canal de salida compartido ("SnapContext Output"). */
let salida = null;
/** Barra de estado con la actividad actual. */
let estadoBarra = null;
/** Archivos seleccionados desde el explorador (contexto visual tipo /add). */
const archivosContexto = new Set();
/** Proceso del servidor web del chat, si está activo. */
let procesoChat = null;

function activar(contexto) {
  salida = vscode.window.createOutputChannel("SnapContext Output");
  contexto.subscriptions.push(salida);

  estadoBarra = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left, 10);
  estadoBarra.text = "$(sparkle) SnapContext";
  estadoBarra.command = "snapcontext.ejecutarConsulta";
  estadoBarra.tooltip = "SnapContext: clic para ejecutar una consulta";
  estadoBarra.show();
  contexto.subscriptions.push(estadoBarra);

  contexto.subscriptions.push(
    vscode.commands.registerCommand("snapcontext.abrirChat", abrirChat),
    vscode.commands.registerCommand("snapcontext.ejecutarConsulta",
                                    ejecutarConsulta),
    vscode.commands.registerCommand("snapcontext.planificar", planificar),
    vscode.commands.registerCommand("snapcontext.configurarApiKey",
                                    configurarApiKey),
    vscode.commands.registerCommand("snapcontext.anadirAlContexto",
                                    anadirAlContexto),
    vscode.commands.registerCommand("snapcontext.limpiarSeleccion",
                                    limpiarSeleccion)
  );

  salida.appendLine(`SnapContext v0.16.0 listo.`);
}

function desactivar() {
  if (procesoChat) {
    procesoChat.kill();
    procesoChat = null;
  }
}

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------
function workspaceActual() {
  const carpetas = vscode.workspace.workspaceFolders;
  if (!carpetas || carpetas.length === 0) {
    throw new Error("Abre una carpeta en VS Code para usar SnapContext.");
  }
  return carpetas[0].uri.fsPath;
}

function config() {
  return vscode.workspace.getConfiguration("snapcontext");
}

/** Argumentos base comunes: proveedor y confirmaciones. */
function argumentosBase() {
  const args = [];
  const proveedor = config().get("provider");
  if (proveedor) {
    args.push("--provider", String(proveedor));
  }
  if (!config().get("confirmar", true)) {
    args.push("--no-confirmar");
  }
  return args;
}

/** Contexto visual: archivos marcados en el explorador. */
function sufijoContexto() {
  if (archivosContexto.size === 0) {
    return [];
  }
  return ["(Revisa especialmente estos archivos: "
          + [...archivosContexto].join(", ") + ")"];
}

/** Variables de entorno con la API key configurada (si la hay). */
function entornoConClave() {
  const env = Object.assign({}, process.env);
  const claveApi = config().get("apiKey", "");
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
function ejecutarSnap(args, titulo) {
  return new Promise((resolve) => {
    let ws;
    try {
      ws = workspaceActual();
    } catch (err) {
      vscode.window.showErrorMessage(err.message);
      resolve(1);
      return;
    }
    const pythonPath = config().get("pythonPath", "python");
    const argv = ["-m", "snapcontext"].concat(args);

    salida.show(true);
    salida.appendLine("");
    salida.appendLine("─".repeat(60));
    salida.appendLine(`▶ ${titulo}`);
    salida.appendLine(`  $ ${path.basename(pythonPath)} ${argv.join(" ")}`);
    salida.appendLine(`  📁 ${ws}`);

    estadoBarra.text = "$(sync~spin) SnapContext: trabajando…";
    const inicio = Date.now();
    const proc = spawn(path.basename(pythonPath), argv, {
      cwd: ws,
      env: entornoConClave(),
      shell: process.platform === "win32",
    });

    const pintar = (buf, esError) => {
      for (const linea of buf.toString().split(/\r?\n/)) {
        if (linea.trim()) {
          salida.appendLine((esError ? "⚠ " : "  ") + linea);
        }
      }
    };
    proc.stdout.on("data", pintar);
    proc.stderr.on("data", (b) => pintar(b, true));

    proc.on("close", (codigo) => {
      const segundos = ((Date.now() - inicio) / 1000).toFixed(1);
      salida.appendLine(`${codigo === 0 ? "✔" : "✖"} Terminado `
                        + `(código ${codigo}, ${segundos}s)`);
      estadoBarra.text = codigo === 0
        ? "$(check) SnapContext"
        : "$(error) SnapContext";
      resolve(codigo === 0 ? 0 : codigo);
    });
    proc.on("error", (err) => {
      salida.appendLine(`✖ No se pudo lanzar Python (${pythonPath}): `
                        + err.message);
      vscode.window.showErrorMessage(
        "SnapContext: no se pudo lanzar Python. Revisa "
        + "\"snapcontext.pythonPath\" y que snapcontext esté instalado "
        + "(pip install snapcontext).");
      estadoBarra.text = "$(error) SnapContext";
      resolve(1);
    });
  });
}

// ---------------------------------------------------------------------------
// Comandos
// ---------------------------------------------------------------------------
/** SnapContext: Abrir chat — webview con la interfaz web existente. */
async function abrirChat() {
  let puerto = 8765;
  while (!(await libre(puerto))) {
    puerto += 1;
  }

  const pythonPath = config().get("pythonPath", "python");
  const ws = workspaceActual();
  const script = (
    "from web.app import arrancar_servidor; import threading, time;"
    + "threading.Thread(target=arrancar_servidor,"
    + `kwargs={'puerto': ${puerto}}, daemon=True).start();`
    + "time.sleep(36000)");
  procesoChat = spawn(path.basename(pythonPath), ["-c", script], {
    cwd: ws,
    env: entornoConClave(),
    shell: process.platform === "win32",
  });
  procesoChat.stdout?.on("data", (b) => salida.appendLine("[chat] " + b));
  procesoChat.stderr?.on("data", (b) => salida.appendLine("[chat] ⚠ " + b));
  procesoChat.on("close", () => { procesoChat = null; });

  await esperarPuerto(puerto, 10000);
  salida.show(true);
  salida.appendLine(`💬 Chat web activo en http://localhost:${puerto}`);

  const panel = vscode.window.createWebviewPanel(
    "snapcontextChat",
    "SnapContext Chat",
    vscode.ViewColumn.One,
    { enableScripts: true },
  );
  panel.webview.html = htmlWebview(`http://localhost:${puerto}`);
  panel.onDidDispose(() => {
    if (procesoChat) { procesoChat.kill(); procesoChat = null; }
  });
}

/** SnapContext: Ejecutar consulta. */
async function ejecutarConsulta() {
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
async function planificar() {
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
async function configurarApiKey() {
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
function anadirAlContexto(uri) {
  if (!uri || !uri.fsPath) { return; }
  try {
    const relativo = path.relative(workspaceActual(), uri.fsPath)
      .replace(/\\/g, "/");
    archivosContexto.add(relativo);
    vscode.commands.executeCommand("setContext",
      "snapcontext.tieneSeleccion", archivosContexto.size > 0);
    vscode.window.showInformationMessage(
      `SnapContext: ${archivosContexto.size} archivo(s) en contexto.`);
    salida.appendLine(`📎 Contexto añadido: ${relativo}`);
  } catch (err) {
    vscode.window.showErrorMessage(err.message);
  }
}

function limpiarSeleccion() {
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
function libre(puerto) {
  return new Promise((resolver) => {
    const servidor = net.createServer();
    servidor.once("error", () => resolver(false));
    servidor.once("listening", () => {
      servidor.close(() => resolver(true));
    });
    servidor.listen(puerto, "127.0.0.1");
  });
}

/** Espera (con timeout) a que un puerto empiece a aceptar conexiones. */
function esperarPuerto(puerto, timeoutMs) {
  const inicio = Date.now();
  function intentar(resolver) {
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
function htmlWebview(url) {
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

module.exports = { activar, desactivar };



