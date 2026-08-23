package com.snapcontext.jetbrains

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.MessageType
import com.intellij.openapi.util.Key
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.ui.JBColor
import javax.swing.JScrollPane
import javax.swing.JTextArea
import javax.swing.SwingUtilities

/**
 * Consola propia y ligera de «SnapContext» por proyecto (área de texto con
 * autoscroll). Evita depender de APIs opcionales del SDK de IntelliJ y funciona
 * en cualquier IDE JetBrains.
 */
class ConsolaSnap(private val area: JTextArea) {

    /** Añade una línea a la consola (thread-safe). */
    fun print(mensaje: String) {
        SwingUtilities.invokeLater {
            area.append(mensaje.ensureTrailingNewline)
            area.caretPosition = area.document.length
        }
    }

    private val String.ensureTrailingNewline: String
        get() = if (endsWith("\n")) this else this + "\n"

    companion object {
        private val CLAVE = Key.create<ConsolaSnap>(
            "com.snapcontext.jetbrains.Consola")

        /** Obtiene (o crea) la consola de SnapContext para el proyecto. */
        fun consolaDe(project: Project): ConsolaSnap? {
            project.getUserData(CLAVE)?.let { return it }

            val herramienta = ToolWindowManager.getInstance(project)
                .getToolWindow("SnapContext") ?: return null
            herramienta.show()

            val factory = herramienta.contentManager.factory ?: return null

            val area = JTextArea().apply {
                isEditable = false
                lineWrap = true
                wrapStyleWord = true
                background = JBColor.background()
                foreground = JBColor.foreground()
            }
            val consola = ConsolaSnap(area)
            val contenido = factory.createContent(
                JScrollPane(area), "Salida", false)
            herramienta.contentManager.addContent(contenido)

            consola.print("🛠 SnapContext listo. Usa Tools → SnapContext para ejecutar consultas.")
            project.putUserData(CLAVE, consola)
            return consola
        }
    }
}
