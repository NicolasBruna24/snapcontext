; ============================================================================
; SnapContext — Instalador NSIS (v3.1.0)
; Empaqueta dist\snapcontext.exe en SnapContext-Setup-<version>.exe
;
;   - Instala en %LOCALAPPDATA%\Programs\SnapContext (por usuario).
;   - Detecta Python: si no existe, guía al usuario (python.org).
;   - Sección opcional: instala/actualiza SnapContext con pip.
;   - Añade la carpeta de instalación al PATH del usuario.
;   - Accesos directos opcionales en Menú Inicio y Escritorio.
;   - Desinstalador completo (borra PATH, accesos y archivos).
;
; Generar:  makensis installer.nsi     (desde la raíz del proyecto)
; ============================================================================

!define PRODUCTO      "SnapContext"
!define VERSION       "3.1.0"
!define EDITOR        "SnapContext Contributors"
!define WEB           "https://github.com/NicolasBruna24/snapcontext"
!define EXE           "snapcontext.exe"
!define INSTDIR_DEF   "$LOCALAPPDATA\Programs\SnapContext"

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "WordFunc.nsh"
!include "WinMessages.nsh"

Name "${PRODUCTO} ${VERSION}"
OutFile "dist\SnapContext-Setup-${VERSION}.exe"
InstallDir "${INSTDIR_DEF}"
InstallDirRegKey HKCU "Software\${PRODUCTO}" "InstallDir"
RequestExecutionLevel user          ; instalación por usuario: sin admin
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\nsis3-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\nsis3-uninstall.ico"

; ── Detección de Python (v3.1.0) ────────────────────────────────────────────
Function .onInit
    ; Busca python.exe en el PATH y en las ubicaciones típicas de PyLauncher.
    nsExec::ExecToLog 'python --version'   ; solo comprueba que exista
    Pop $0
    ${If} $0 != 0
        ClearErrors
        ReadRegStr $R0 HKCU "Software\Python\PythonCore" "Version"
        ${If} ${Errors}
            MessageBox MB_YESNO|MB_ICONQUESTION \
                "No se detectó Python en este equipo.$\n$\nSnapContext necesita Python 3.9 o superior para el modo pip (el ejecutable incluido funciona sin él).$\n$\n¿Abrir https://www.python.org/downloads/ para descargarlo?" \
                IDNO +2
            ExecShell "open" "https://www.python.org/downloads/"
        ${EndIf}
    ${EndIf}
FunctionEnd

; ── Páginas ─────────────────────────────────────────────────────────────────
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--version"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Spanish"

; ── Secciones ───────────────────────────────────────────────────────────────
Section "${PRODUCTO} (obligatorio)" SecPrincipal
    SectionIn RO
    SetOutPath "$INSTDIR"
    File "dist\${EXE}"

    ; Registrar desinstalador.
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\${PRODUCTO}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTO}" \
        "DisplayName" "${PRODUCTO} ${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTO}" \
        "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTO}" \
        "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTO}" \
        "Publisher" "${EDITOR}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTO}" \
        "URLInfoAbout" "${WEB}"

    ; Añadir al PATH del usuario (sin duplicados), como hace --setup-path.
    ReadRegStr $R0 HKCU "Environment" "Path"
    ${WordFind} "$R0" "$INSTDIR" "E+1{" $R1
    IfErrors 0 +2               ; ya está → no tocar
        WriteRegExpandStr HKCU "Environment" "Path" "$INSTDIR;$R0"
    ; Avisar al sistema de que cambió el entorno (WM_SETTINGCHANGE).
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
SectionEnd

Section "Acceso directo en Menú Inicio" SecMenu
    CreateDirectory "$SMPROGRAMS\${PRODUCTO}"
    CreateShortCut "$SMPROGRAMS\${PRODUCTO}\${PRODUCTO}.lnk" "$INSTDIR\${EXE}"
    CreateShortCut "$SMPROGRAMS\${PRODUCTO}\Desinstalar ${PRODUCTO}.lnk" \
        "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "Acceso directo en Escritorio" SecEscritorio
    CreateShortCut "$DESKTOP\${PRODUCTO}.lnk" "$INSTDIR\${EXE}"
SectionEnd

; v3.1.0: instalación vía pip (requiere Python). Con esto el comando
; `snapcontext` queda disponible en cualquier terminal, no solo el .exe.
Section /o "Instalar/actualizar con pip (recomendado)" SecPip
    DetailPrint "Instalando SnapContext desde PyPI con pip..."
    nsExec::ExecToLog 'python -m pip install --upgrade snapcontext'
    Pop $0
    ${If} $0 = 0
        DetailPrint "pip: SnapContext instalado correctamente."
        ; Añadir %USERPROFILE%\.local\bin y Scripts de Python al PATH si
        ; `snapcontext` no es accesible todavía.
        nsExec::ExecToLog 'python -m snapcontext --setup-path'
        Pop $1
    ${Else}
        DetailPrint "pip falló ($0). Instala Python 3.9+ y ejecuta: python -m pip install snapcontext"
    ${EndIf}
SectionEnd

; Descripciones de componentes.
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecMenu} \
        "Crea un acceso directo en el Menú Inicio."
    !insertmacro MUI_DESCRIPTION_TEXT ${SecEscritorio} \
        "Crea un acceso directo en el Escritorio."
    !insertmacro MUI_DESCRIPTION_TEXT ${SecPip} \
        "Instala/actualiza SnapContext con pip (requiere Python 3.9+). Deja el comando 'snapcontext' disponible en cualquier terminal."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ── Desinstalador ───────────────────────────────────────────────────────────
Section "Uninstall"
    ; Quitar del PATH del usuario.
    ReadRegStr $R0 HKCU "Environment" "Path"
    ${WordReplace} "$R0" "$INSTDIR;" "" "+" $R1
    ${WordReplace} "$R1" "$INSTDIR" "" "+" $R2
    WriteRegExpandStr HKCU "Environment" "Path" "$R2"
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000

    ; Accesos directos.
    RMDir /r "$SMPROGRAMS\${PRODUCTO}"
    Delete "$DESKTOP\${PRODUCTO}.lnk"

    ; Archivos y registro. La configuración (~\.snapcontext) se conserva:
    ; contiene claves API e historial del usuario.
    Delete "$INSTDIR\${EXE}"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTO}"
    DeleteRegKey HKCU "Software\${PRODUCTO}"
SectionEnd
