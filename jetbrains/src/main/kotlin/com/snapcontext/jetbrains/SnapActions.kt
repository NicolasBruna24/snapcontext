package com.snapcontext.jetbrains

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import javax.swing.SwingUtilities

/** Tools → SnapContext → «Ejecutar consulta…». */
class RunQueryAction : AnAction(
    "Ejecutar consulta…",
    "Ejecuta una consulta de SnapContext sobre el proyecto", null) {

    override fun actionPerformed(evento: AnActionEvent) {
        val project = evento.project ?: return
        val consulta = AccionUtil.pedirConsulta(project, "SnapContext — Consulta")
            ?: return
        val contexto = SnapContextService.sufijoContexto()
        AccionUtil.ejecutarSnap(evento, listOf("\"$consulta$contexto\""), "consulta")
    }
}

/** Tools → SnapContext → «Planificar tarea…» (--plan --auto). */
class PlanAction : AnAction(
    "Planificar tarea…", "Ejecuta el planificador (--plan) con la consulta indicada", null) {

    override fun actionPerformed(evento: AnActionEvent) {
        val project = evento.project ?: return
        val consulta = AccionUtil.pedirConsulta(project, "SnapContext — Planificar")
            ?: return
        val contexto = SnapContextService.sufijoContexto()
        AccionUtil.ejecutarSnap(evento,
            listOf("--plan", "\"$consulta$contexto\"", "--auto"), "planificador")
    }
}

/** Tools → SnapContext → «Corregir con bucle de pruebas…» (--test-loop). */
class TestLoopAction : AnAction(
    "Corregir con bucle de pruebas…",
    "Ejecuta la consulta con --test-loop (Aider → pruebas → reparar)", null) {

    override fun actionPerformed(evento: AnActionEvent) {
        val project = evento.project ?: return
        val consulta = AccionUtil.pedirConsulta(project, "SnapContext — Fix con pruebas")
            ?: return
        val contexto = SnapContextService.sufijoContexto()
        AccionUtil.ejecutarSnap(evento,
            listOf("\"$consulta$contexto\"", "--test-loop"), "bucle de pruebas")
    }
}

/** Tools → SnapContext → «Abrir interfaz web»: arranca --web y abre el navegador. */
class OpenWebAction : AnAction(
    "Abrir interfaz web", "Arranca snapcontext --web y lo abre en el navegador", null) {

    override fun actionPerformed(evento: AnActionEvent) {
        val project = evento.project ?: return
        val consola = ConsolaSnap.consolaDe(project)
        val raiz = project.basePath ?: return

        Thread {
            try {
                val comando = SnapContextService.construirComando(listOf("--web"), raiz)
                val pb = ProcessBuilder(comando).directory(File(raiz))
                pb.redirectErrorStream(true)
                val proceso = pb.start()
                val lector = BufferedReader(
                    InputStreamReader(proceso.inputStream, Charsets.UTF_8))
                var abierto = false
                while (true) {
                    val linea = lector.readLine() ?: break
                    consola?.let { c ->
                        SwingUtilities.invokeLater { c.print(linea) }
                    }
                    // Abre el navegador en cuanto Uvicorn está escuchando.
                    if (!abierto && ("Uvicorn running" in linea || "8000" in linea)) {
                        abierto = true
                        SwingUtilities.invokeLater {
                            com.intellij.ide.BrowserUtil.browse("http://localhost:8000")
                        }
                    }
                }
            } catch (exc: Exception) {
                consola?.print("✖ Error arrancando la web: ${exc.message}")
            }
        }.apply { isDaemon = true }.start()

        Messages.showInfoMessage(
            project,
            "La interfaz web se está arrancando.\nSe abrirá en http://localhost:8000\n\n" +
            "Deténla desde la consola «SnapContext» o cerrando el IDE.",
            "SnapContext — Interfaz web")
    }
}
