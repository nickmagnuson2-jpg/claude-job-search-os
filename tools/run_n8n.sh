#!/bin/bash
# Start n8n with Execute Command node enabled.
#
# By default n8n 2.x excludes n8n-nodes-base.executeCommand for security.
# Setting NODES_EXCLUDE=[] restores access on this local machine.
#
# Equivalent of tools/run_n8n.bat for macOS.
#
# Usage: bash tools/run_n8n.sh

export NODES_EXCLUDE="[]"
echo "Starting n8n with NODES_EXCLUDE=$NODES_EXCLUDE"
echo "Dashboard: http://localhost:5678"
echo "Press Ctrl+C to stop"
n8n start
