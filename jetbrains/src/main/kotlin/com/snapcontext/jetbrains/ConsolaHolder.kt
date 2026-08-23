package com.snapcontext.jetbrains

import com.intellij.execution.ui.ConsoleView
import com.intellij.execution.ui.ConsoleViewContentType
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowAnchor
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.ui.content.Content
import com.intellij.ui.content.ContentFactory

/**
 * Gestiona la consola «SnapContext» por proyecto: crea la tool window
 * registrada en plugin.xml y guarda el [ConsoleView] en los datos del proyecto.
 */
object ConsolaHolder {

    private val CLAVE = com.intellij.openapi.util.Key.create<ConsoleView>(
        "com.snapcontext.jetbrains.Consola")

    /** Obtiene (o crea) la consola de SnapContext para el proyecto. */
    fun consolaDe(project: Project): ConsoleView? {
        project.getUserData(CLAVE)?.let { return it }

        val herramienta: ToolWindow = ToolWindowManager.getInstance(project)
            .getToolWindow("SnapContext") ?: return null
        herramienta.anchor = ToolWindowAnchor.BOTTOM
        herramienta.show()

        val factory = herramienta.contentManager.factory ?: return null
        val consola = factory.createConsoleView("SnapContext") as? ConsoleView
            ?: return null

        val contenido: Content = ContentFactory.SERVICE.getInstance()
            .createContent(consola.component, "Salida", false)
        herramienta.contentManager.addContent(contenido)
        consola.print("🛠 SnapContext listo. Usa Tools → SnapContext para ejecutar consultas.\n",
            ConsoleViewContentType.SYSTEM_OUTPUT)
        project.putUserData(CLAVE, consola)
        return consola
    }
}
