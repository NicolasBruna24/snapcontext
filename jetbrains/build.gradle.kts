// ============================================================================
// SnapContext — Extensión para IntelliJ IDEA / PyCharm (v2.2.0)
//
// Compilar plugin:   .\gradlew buildPlugin      → build/distributions/*.zip
// Probar en IDE:     .\gradlew runIde
//
// Requiere JDK 17+ (JAVA_HOME) y conexión a Internet la primera vez (Gradle
// descarga el SDK de IntelliJ Community indicado abajo).
// ============================================================================

plugins {
    id("java")
    id("org.jetbrains.kotlin.jvm") version "1.9.24"
    id("org.jetbrains.intellij") version "1.17.3"
}

group = "com.snapcontext"
version = "6.18.0"

repositories {
    mavenCentral()
}

intellij {
    // IDE base para compilar: IntelliJ IDEA Community (compatible con PyCharm).
    version.set("2023.2.6")
    type.set("IC")                       // IC = IntelliJ Community
    plugins.set(listOf())
}

kotlin {
    jvmToolchain(17)
}

tasks {
    withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile> {
        kotlinOptions.jvmTarget = "17"
    }

    patchPluginXml {
        sinceBuild.set("232")            // 2023.2
        untilBuild.set("243.*")          // hasta 2024.3
        changeNotes.set(
            "1.7.0 — Primera versión: ejecutar consultas, planificar, abrir la " +
            "interfaz web, añadir archivos al contexto desde el explorador y " +
            "salida en consola dedicada."
        )
    }

    buildSearchableOptions {
        enabled = false                   // acelera el build del plugin
    }
}
