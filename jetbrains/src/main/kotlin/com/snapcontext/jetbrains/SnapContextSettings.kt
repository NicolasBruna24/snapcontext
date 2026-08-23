package com.snapcontext.jetbrains

import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.ServiceManager
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage
import com.intellij.util.xmlb.XmlSerializerUtil

/** Ajustes persistentes del plugin (Settings → Tools → SnapContext). */
@State(name = "SnapContextSettings", storages = [Storage("snapcontext.xml")])
class SnapContextSettings : PersistentStateComponent<SnapContextSettings> {
    /** Comando para invocar SnapContext: `python -m snapcontext`, `snapcontext`, … */
    var comando: String = "python -m snapcontext"

    /** Proveedor de IA por defecto (vacío = usar el guardado en ~/.snapcontext). */
    var proveedor: String = ""

    /** Si es false, se pasa --no-confirmar (modo autónomo). */
    var confirmar: Boolean = true

    /** Clave de API opcional; si no está vacía se exporta como GEMINI_API_KEY. */
    var claveApi: String = ""

    override fun getState(): SnapContextSettings = this

    override fun loadState(estado: SnapContextSettings) {
        XmlSerializerUtil.copyBean(estado, this)
    }

    companion object {
        fun getInstance(): SnapContextSettings =
            ServiceManager.getService(SnapContextSettings::class.java)
    }
}
