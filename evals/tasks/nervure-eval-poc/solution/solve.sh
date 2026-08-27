#!/bin/bash
set -eu
python - <<'PY'
from pathlib import Path
path = Path('/workspace/calculator/statistics.py')
text = path.read_text()
text = text.replace('sum(values) / (len(values) - 1)', 'sum(values) / len(values)')
path.write_text(text)
PY
