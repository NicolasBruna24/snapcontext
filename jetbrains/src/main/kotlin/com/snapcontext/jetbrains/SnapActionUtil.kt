package com.snapcontext.jetbrains

import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import javax.swing.SwingUtilities

/** Utilidades compartidas por todas las acciones de SnapContext. */
object AccionUtil {

    /** Ejecuta SnapContext en segundo plano, volcando la salida a la consola. */
    fun ejecutarSnap(evento: AnActionEvent, args: List<String>, titulo: String) {
        val project = evento.project ?: return
        val raiz = project.basePath ?: return
        val consola = ConsolaSnap.consolaDe(project) ?: run {
            Messages.showErrorDialog(
                "No se pudo abrir la herramienta «SnapContext».", "SnapContext")
            return
        }
        val comando = try {
            SnapContextService.construirComando(args, raiz)
        } catch (exc: IllegalStateException) {
            Messages.showErrorDialog(exc.message, "SnapContext")
            return
        }

        consola.print("\n\$ ${comando.joinToString(" ")}   (cwd: $raiz)")

        ProgressManager.getInstance().run(object :
            Task.Backgroundable(project, "SnapContext: $titulo", true) {
            @Volatile private var proceso: Process? = null

            override fun run(indicador: ProgressIndicator) {
                val pb = ProcessBuilder(comando).directory(File(raiz))
                pb.redirectErrorStream(true)
                val clave = SnapContextSettings.getInstance().claveApi.trim()
                if (clave.isNotEmpty()) {
                    pb.environment()["GEMINI_API_KEY"] = clave
                    pb.environment()["ANTHROPIC_API_KEY"] = clave
                    pb.environment()["DEEPSEEK_API_KEY"] = clave
                    pb.environment()["GROQ_API_KEY"] = clave
                }

                try {
                    proceso = pb.start()
                } catch (exc: Exception) {
                    imprimir(consola,
                        "✖ No se pudo lanzar SnapContext (${exc.message}).\n" +
                        "  Revisa Settings → Tools → SnapContext → Comando.")
                    return
                }

                val lector = BufferedReader(
                    InputStreamReader(proceso!!.inputStream, Charsets.UTF_8))
                while (true) {
                    if (indicador.isCanceled) {
                        proceso!!.destroy()
                        imprimir(consola, "\n⚠ Tarea cancelada por el usuario.")
                        break
                    }
                    val linea = lector.readLine() ?: break
                    imprimir(consola, linea)
                }
            }

            override fun onCancel() { proceso?.destroy() }

            override fun onFinished() {
                val codigo = proceso?.exitValue() ?: -1
                imprimir(consola,
                    if (codigo == 0) "\n✔ SnapContext terminó correctamente.\n"
                    else "\n✖ SnapContext terminó con código $codigo.\n")
            }

            private fun imprimir(cons: ConsolaSnap, mensaje: String) {
                SwingUtilities.invokeLater { cons.print(mensaje) }
            }
        })
    }

    /** Pide una consulta al usuario; devuelve null si cancela. */
    fun pedirConsulta(project: Project, titulo: String): String? =
        Messages.showInputDialog(project, "Consulta para SnapContext:", titulo,
            Messages.getQuestionIcon())?.trim()?.takeIf { it.isNotEmpty() }
}
