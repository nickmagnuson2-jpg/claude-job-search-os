@echo off
REM Start n8n with Execute Command node enabled.
REM
REM By default n8n 2.x excludes n8n-nodes-base.executeCommand for security.
REM Setting NODES_EXCLUDE=[] restores access on this local machine.
REM
REM Usage: double-click, or run from any terminal.

set NODES_EXCLUDE=[]
echo Starting n8n with NODES_EXCLUDE=%NODES_EXCLUDE%
echo Dashboard: http://localhost:5678
n8n start
