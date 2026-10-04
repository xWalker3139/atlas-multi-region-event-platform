#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda
mkdir -p build/lambda dist
python -m pip install --platform manylinux2014_x86_64 --only-binary=:all: --implementation cp --python-version 3.12 --target build/lambda -r requirements-aws.txt
cp -r atlas build/lambda/
curl --fail --location --proto '=https' --tlsv1.2 https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o build/lambda/rds-ca.pem
python - <<'PY'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
with ZipFile('dist/atlas-lambda.zip','w',ZIP_DEFLATED) as z:
    for p in sorted(Path('build/lambda').rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            z.write(p,p.relative_to('build/lambda'))
PY
