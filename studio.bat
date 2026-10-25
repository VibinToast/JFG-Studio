@echo off
rem Opens JFG-Studio. With no arguments it opens the sample planet; otherwise the arguments go to jfgstudio
rem (for example: studio.bat C:\path\my_planet.jfgproj, or studio.bat test samples\gemini.jfgproj).
cd /d "%~dp0"
if "%~1"=="" (
    ".venv\Scripts\jfgstudio.exe" gui samples\gemini.jfgproj
) else (
    ".venv\Scripts\jfgstudio.exe" %*
)
if errorlevel 1 pause
