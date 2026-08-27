#!/bin/sh
set -eu
python /solution/fix.py
python -m unittest discover -s /workspace/tests -p "test_*.py" -v
