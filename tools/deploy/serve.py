"""Portable Windows entry point; never depends on the caller's working directory."""
import argparse
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
os.chdir(root)
sys.path.insert(0, str(root))

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=3000)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()

# This portable installation owns its platform keys, not another app's environment.
config = root / 'API/.env'
if config.is_file():
    for line in config.read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            if name.startswith(('API_PROVIDER_', 'COMFLY_', 'MODELSCOPE_', 'RUNNINGHUB_', 'ARK_', 'VOLCENGINE_')):
                os.environ[name.strip()] = value.strip().strip('\"').strip("'")

import main
import uvicorn

if args.check:
    print('DEPENDENCIES_OK')
else:
    uvicorn.run(main.app, host='127.0.0.1', port=args.port,
                ws_ping_interval=None, ws_ping_timeout=None)
