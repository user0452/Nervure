#!/bin/sh
set +e
python -m unittest discover -s /tests -p 'test_*.py' -v
status=$?
mkdir -p /logs/verifier
if [ "$status" -eq 0 ]; then
  printf '1
' > /logs/verifier/reward.txt
else
  printf '0
' > /logs/verifier/reward.txt
fi
exit 0
