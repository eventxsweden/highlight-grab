@echo off
echo Highlight Grab startar...
python "%~dp0highlight_grab.py"
if errorlevel 1 pause
