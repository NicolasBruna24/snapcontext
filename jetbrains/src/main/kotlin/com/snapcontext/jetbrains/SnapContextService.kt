package com.snapcontext.jetbrains

import java.io.File
import java.nio.charset.Charset

/**
 * Núcleo del plugin: construye el comando de la CLI de SnapContext (misma
 * estrategia que la extensión de VS Code) y lo lanza con [ProcessBuilder].
 */
object SnapContextService {

    /** Archivos marcados como contexto (equivalente a /add en la CLI). */
    val archivosContexto = LinkedHashSet<String>()

    fun limpiarContexto() = archivosContexto.clear()

    /**
     * Construye el comando completo a partir de los ajustes y los argumentos.
     * El primer token del ajuste «comando» es el ejecutable; el resto, flags.
     */
    fun construirComando(args: List<String>, raiz: String): List<String> {
        val ajustes = SnapContextSettings.getInstance()
        val partes = ajustes.comando.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        if (partes.isEmpty()) throw IllegalStateException(
            "El comando de SnapContext está vacío (Settings → Tools → SnapContext).")

        return mutableListOf<String>().apply {
            addAll(partes)
            addAll(args)
            add("--directorio"); add(raiz)
            val proveedor = ajustes.proveedor.trim()
            if (proveedor.isNotEmpty()) { add("--provider"); add(proveedor) }
            if (!ajustes.confirmar) add("--no-confirmar")
        }
    }

    /** Sufijo de contexto visual, igual que hace la extensión de VS Code. */
    fun sufijoContexto(): String =
        if (archivosContexto.isEmpty()) ""
        else " (Revisa especialmente estos archivos: ${archivosContexto.joinToString(", ")})"

    private fun entornoConClave(base: MutableMap<String, String>) {
        val clave = SnapContextSettings.getInstance().claveApi.trim()
        if (clave.isNotEmpty()) {
            base["GEMINI_API_KEY"] = clave
            base["ANTHROPIC_API_KEY"] = clave
            base["DEEPSEEK_API_KEY"] = clave
            base["GROQ_API_KEY"] = clave
        }
    }

    /**
     * Lanza el proceso con stdout+stderr unidos. La lectura se hace en otro
     * hilo mediante [onLinea]; devuelve null si no se pudo arrancar.
     */
    fun lanzar(args: List<String>, raiz: String,
               onLinea: (String) -> Unit,
               onFin: (Int) -> Unit): Process? {
        val comando = construirComando(args, raiz)
        val pb = ProcessBuilder(comando).directory(File(raiz))
        pb.redirectErrorStream(true)
        pb.environment().let { entornoConClave(it) }

        val proceso = try {
            pb.start()
        } catch (exc: Exception) {
            onLinea("✖ No se pudo lanzar SnapContext (${exc.message}).")
            onLinea("  Revisa Settings → Tools → SnapContext → Comando.")
            return null
        }

        Thread {
            proceso.inputStream.bufferedReader(Charset.defaultCharset()).useLines { lineas ->
                lineas.forEach { linea -> onLinea(linea) }
            }
            onFin(proceso.exitValue())
        }.apply { isDaemon = true }.start()

        return proceso
    }
}
