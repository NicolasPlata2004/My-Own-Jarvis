@echo off
rem Abre Jarvis sin ventana de consola.
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
