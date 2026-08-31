/* interactive.js — Centro de control interactivo de SnapContext (v6.5.0).
 *
 * - Timeline de ReAct: escucha eventos WebSocket `react_step` y muestra cada
 *   paso como tarjeta coloreada (🟡 pensamiento / 🔵 acción / 🟢 observación /
 *   🔴 error) con marca de tiempo relativa y detalles expandibles.
 * - Panel de control: estado del agente (`agent_status`), contador de pasos y
 *   tiempo total, logs estructurados (`log_interactivo`).
 * - Diff viewer: ante un `diff_conflict` abre un modal con Monaco en modo
 *   diff (original ⇆ propuesto) con "Aceptar todo", "Rechazar todo" y
 *   "Aceptar líneas seleccionadas"; envía la respuesta por el mismo WebSocket.
 */
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const timeline = $("timeline"), logs = $("logs");
  const estadoEl = $("estado-agente"), contadorEl = $("contador-pasos");
  const tiempoEl = $("tiempo-total"), sinConexion = $("sin-conexion");
  const modal = $("modal-diff");

  let pasos = 0, inicio = Date.now();
  let editorDiff = null, conflictoActual = null;
  setInterval(() => {
    tiempoEl.textContent = Math.round((Date.now() - inicio) / 1000) + "s";
    document.querySelectorAll(".paso .tiempo").forEach((el) => {
      el.textContent = "hace " + Math.round((Date.now() - +el.dataset.t) / 1000) + "s";
    });
  }, 1000);

  function log(nivel, texto) {
    const div = document.createElement("div");
    div.className = "log-" + (nivel || "info");
    div.textContent = "[" + new Date().toLocaleTimeString() + "] " + texto;
    logs.appendChild(div);
    logs.scrollTop = logs.scrollHeight;
  }

  const ICONOS = { pensamiento: "🟡 💭 Pensamiento", accion: "🔵 ⚡ Acción",
                   observacion: "🟢 👁 Observación", error: "🔴 ✖ Error" };

  function agregarPaso(ev) {
    pasos++; contadorEl.textContent = pasos;
    const fase = ev.fase || "observacion";
    const div = document.createElement("div");
    div.className = "paso " + fase;
    const h = document.createElement("h3");
    h.className = "expander";
    h.innerHTML = (ICONOS[fase] || fase) +
      " <small>#" + (ev.iteracion || pasos) + "</small>" +
      '<span class="tiempo" data-t="' + Date.now() + '">hace 0s</span>';
    const cuerpo = document.createElement("pre");
    cuerpo.textContent = ev.contenido || "";
    const detalles = document.createElement("div");
    detalles.className = "detalles";
    if (ev.argumentos) {
      detalles.innerHTML = "<b>Argumentos:</b>";
      const pre = document.createElement("pre");
      try { pre.textContent = JSON.stringify(JSON.parse(ev.argumentos), null, 2); }
      catch (e) { pre.textContent = ev.argumentos; }
      detalles.appendChild(pre);
    }
    h.onclick = () => div.classList.toggle("expandido");
    div.append(h, cuerpo, detalles);
    timeline.prepend(div);
  }

  /* ---------------- Diff viewer (Monaco, modo diff) ---------------- */
  function abrirModalDiff(ev) {
    conflictoActual = ev;
    $("diff-ruta").textContent = ev.ruta || "(archivo)";
    modal.classList.add("abierto");
    const crear = () => {
      if (editorDiff) { editorDiff.dispose(); editorDiff = null; }
      editorDiff = monaco.editor.createDiffEditor($("contenedor-diff"), {
        readOnly: false, originalEditable: false, renderSideBySide: true,
        theme: "vs-dark", automaticLayout: true,
      });
      editorDiff.setModel({
        original: monaco.editor.createModel(ev.original || "", "plaintext"),
        modified: monaco.editor.createModel(ev.propuesto || "", "plaintext"),
      });
    };
    if (window.monaco) { crear(); return; }
    require.config({ paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.34.1/min/vs" } });
    require(["vs/editor/editor.main"], crear);
  }

  function responderDiff(decision, contenido) {
    if (!conflictoActual || !ws || ws.readyState !== 1) { cerrarModalDiff(); return; }
    ws.send(JSON.stringify({ tipo: "diff_respuesta", id: conflictoActual.id,
                             decision: decision,
                             contenido: contenido != null ? contenido
                                       : conflictoActual.propuesto || "" }));
    log("info", "Diff '" + conflictoActual.ruta + "': " + decision);
    cerrarModalDiff();
  }

  function cerrarModalDiff() {
    modal.classList.remove("abierto");
    conflictoActual = null;
  }

  $("btn-aceptar").onclick = () => responderDiff("aceptar", null);
  $("btn-rechazar").onclick = () => responderDiff("rechazar", null);
  $("btn-lineas").onclick = () => {
    if (!editorDiff) { responderDiff("rechazar", null); return; }
    const mod = editorDiff.getModifiedEditor();
    const sel = mod.getSelection();
    const modelo = mod.getModel();
    let texto;
    if (sel && !sel.isEmpty()) texto = modelo.getValueInRange(sel);
    else texto = modelo.getValue();
    responderDiff("aceptar", texto);
  };

  /* ---------------- WebSocket ---------------- */
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  let ws = new WebSocket(proto + location.host + "/ws/interactive");

  ws.onopen = () => { sinConexion.style.display = "none"; log("info", "Conectado al centro de control."); };
  ws.onclose = () => { sinConexion.style.display = "inline"; log("error", "Conexión perdida."); };
  ws.onerror = () => { sinConexion.style.display = "inline"; };

  ws.onmessage = (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
    switch (ev.tipo) {
      case "react_step": agregarPaso(ev); break;
      case "agent_status":
        estadoEl.textContent = ev.estado || "…";
        if (ev.detalle) log("info", ev.estado + ": " + ev.detalle);
        break;
      case "log_interactivo": log(ev.nivel, ev.texto); break;
      case "diff_conflict":
        log("warning", "Conflicto de parche en " + ev.ruta);
        abrirModalDiff(ev);
        break;
      default: break;
    }
  };
})();
