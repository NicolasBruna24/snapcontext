package com.snapcontext.jetbrains

import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory

/**
 * Fábrica de la herramienta «SnapContext» (registrada en plugin.xml).
 * Deja una consola vacía disponible; las acciones escriben en ella a través
 * de [ConsolaHolder].
 */
class SnapContextToolWindowFactory : ToolWindowFactory {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        // El ConsoleView real se crea perezosamente desde ConsolaHolder cuando
        // el usuario ejecuta su primera acción (evita costes al abrir el IDE).
        val aviso = ContentFactory.SERVICE.getInstance().createContent(
            javax.swing.JLabel(
                "Ejecuta Tools → SnapContext → «Ejecutar consulta…» para empezar."),
            "Inicio", false)
        toolWindow.contentManager.addContent(aviso)
    }

    override fun shouldBeAvailable(project: Project): Boolean = true
}
