@echo off
rem Background deploy wrapper: run upload_tree.py with log capture.
cd /d "%~dp0.."
.venv\Scripts\python scripts\upload_tree.py > deploy_resume_log.txt 2>&1
