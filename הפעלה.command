#!/bin/bash
cd "$(dirname "$0")"
echo "מפעיל את ReportAI..."
python3 app.py &
sleep 1.5
open http://localhost:5001
echo "האפליקציה פועלת על http://localhost:5001"
wait
