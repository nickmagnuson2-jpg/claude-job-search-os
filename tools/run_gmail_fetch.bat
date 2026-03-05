@echo off
cd /d C:\Users\chris\Documents\Projects\Nick-claude-resume-coach
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
python tools\gmail_fetch.py --repo-root . --label-id Label_7175134973725917628 >> logs\gmail_fetch.log 2>&1
