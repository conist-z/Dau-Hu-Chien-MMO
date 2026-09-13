@echo off
cd /d "%~dp0.."
call .venv\Scripts\python scripts/upload_tree.py --only . --only game --only persistence --only discord_ui --only rendering >> deploy_log.txt 2>&1
echo HOTBAR_UPLOAD_DONE >> deploy_log.txt
