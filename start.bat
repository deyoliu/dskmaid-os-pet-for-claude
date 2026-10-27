@echo off
rem Start the inner-OS pet without a console window. A second launch is ignored (lock file).
cd /d "%~dp0"
start "" pythonw run.pyw
