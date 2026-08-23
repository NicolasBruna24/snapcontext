package com.snapcontext.jetbrains

import com.intellij.execution.ui.ConsoleViewContentType
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import java.io.File
import java.nio.file.Paths

/** Menú contextual del explorador: añadir los archivos seleccionados al contexto. */
class AddToContextAction : AnAction(
    "SnapContext: Añadir al contexto",
    "Marca estos archivos como contexto prioritario para las consultas", null) {

    override fun update(evento: AnActionEvent) {
        evento.presentation.isEnabled =
            evento.getData(CommonDataKeys.VIRTUAL_FILE_ARRAY)?.isNotEmpty() == true
    }

    override fun actionPerformed(evento: AnActionEvent) {
        val archivos = evento.getData(CommonDataKeys.VIRTUAL_FILE_ARRAY) ?: return
        val project = evento.project ?: return
        val raiz = project.basePath?.let { Paths.get(it) }

        archivos.forEach { vf ->
            // Ruta relativa al proyecto cuando sea posible (como espera la CLI).
            val nio = vf.toNioPath()
            val ruta = if (raiz != null && nio.startsWith(raiz))
                raiz.relativize(nio).toString().replace('\\', '/')
            else vf.path
            SnapContextService.archivosContexto.add(ruta)
        }

        val lista = SnapContextService.archivosContexto.joinToString("\n")
        ConsolaSnap.consolaDe(project)?.print(
            "✔ Contexto (${SnapContextService.archivosContexto.size}):\n$lista")
        Messages.showInfoMessage(project,
            "Archivos en contexto (${SnapContextService.archivosContexto.size}):\n$lista",
            "SnapContext")
    }
}

/** Tools → SnapContext → «Limpiar archivos de contexto». */
class ClearContextAction : AnAction(
    "Limpiar archivos de contexto", "Vacía la lista de archivos añadidos al contexto", null) {

    override fun actionPerformed(evento: AnActionEvent) {
        val project: Project = evento.project ?: return
        SnapContextService.limpiarContexto()
        ConsolaSnap.consolaDe(project)?.print("✔ Contexto vaciado.")
        Messages.showInfoMessage(project, "Contexto vaciado.", "SnapContext")
    }
}
