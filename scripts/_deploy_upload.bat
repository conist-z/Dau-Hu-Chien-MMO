@echo off
rem Detached upload launcher (spawned via WMI; CWD-safe).
cd /d "%~dp0.."
".venv\Scripts\python.exe" scripts\upload_tree.py %* > temp_fx_upload_log.txt 2> temp_fx_upload_log.err
