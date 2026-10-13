"""Verify the freshly extracted development snapshot before editing it."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'SHA256SUMS.json').read_text(encoding='utf-8'))
failed = []
for name, expected in manifest.items():
    file = root / name
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
        failed.append(name)
if failed:
    raise SystemExit('Missing or modified files:\n' + '\n'.join(failed))
print(f'OK: {len(manifest)} files verified.')
