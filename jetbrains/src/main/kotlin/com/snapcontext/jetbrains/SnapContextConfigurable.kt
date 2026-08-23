package com.snapcontext.jetbrains

import com.intellij.openapi.options.Configurable
import javax.swing.JComponent
import javax.swing.JPanel
import javax.swing.JTextField
import javax.swing.JCheckBox
import javax.swing.JLabel
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import java.awt.Insets

/**
 * Página Settings → Tools → SnapContext: comando CLI, proveedor, confirmación
 * y clave de API opcional.
 */
class SnapContextConfigurable : Configurable {

    private lateinit var campoComando: JTextField
    private lateinit var campoProveedor: JTextField
    private lateinit var checkConfirmar: JCheckBox
    private lateinit var campoClave: JTextField

    override fun getDisplayName(): String = "SnapContext"

    override fun createComponent(): JComponent {
        val panel = JPanel(GridBagLayout())
        val c = GridBagConstraints().apply {
            insets = Insets(6, 6, 6, 6)
            fill = GridBagConstraints.HORIZONTAL
        }

        c.gridx = 0; c.gridy = 0; c.weightx = 0.0
        panel.add(JLabel("Comando de SnapContext:"), c)
        campoComando = JTextField(30)
        c.gridx = 1; c.weightx = 1.0
        panel.add(campoComando, c)

        c.gridx = 0; c.gridy = 1; c.weightx = 0.0
        panel.add(JLabel("Proveedor (--provider):"), c)
        campoProveedor = JTextField(30)
        c.gridx = 1; c.weightx = 1.0
        panel.add(campoProveedor, c)

        c.gridx = 0; c.gridy = 2; c.weightx = 0.0
        panel.add(JLabel("Clave API (GEMINI_API_KEY):"), c)
        campoClave = JTextField(30)
        c.gridx = 1; c.weightx = 1.0
        panel.add(campoClave, c)

        checkConfirmar = JCheckBox("Pedir confirmación antes de acciones sensibles")
        c.gridx = 0; c.gridy = 3; c.gridwidth = 2
        panel.add(checkConfirmar, c)

        return panel
    }

    override fun isModified(): Boolean {
        val ajustes = SnapContextSettings.getInstance()
        return campoComando.text != ajustes.comando ||
               campoProveedor.text != ajustes.proveedor ||
               campoClave.text != ajustes.claveApi ||
               checkConfirmar.isSelected != ajustes.confirmar
    }

    override fun apply() {
        val ajustes = SnapContextSettings.getInstance()
        ajustes.comando = campoComando.text.trim()
        ajustes.proveedor = campoProveedor.text.trim()
        ajustes.claveApi = campoClave.text.trim()
        ajustes.confirmar = checkConfirmar.isSelected
    }

    override fun reset() {
        val ajustes = SnapContextSettings.getInstance()
        campoComando.text = ajustes.comando
        campoProveedor.text = ajustes.proveedor
        campoClave.text = ajustes.claveApi
        checkConfirmar.isSelected = ajustes.confirmar
    }
}
