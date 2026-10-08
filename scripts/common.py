"""Standard-library helpers. No network, dependency installs, or implicit approvals."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(p)


def run(args, cwd=None):
    result = subprocess.run([str(x) for x in args], text=True, capture_output=True, cwd=cwd)
    if result.returncode:
        raise RuntimeError('Command failed: ' + str(args[0]) + '\n' + result.stderr[-6000:])
    return result.stdout


def probe(path, count=False):
    args = ['ffprobe', '-v', 'error']
    if count:
        args += ['-count_frames']
    return json.loads(run(args + ['-show_streams', '-show_format', '-of', 'json', path]))


def fingerprint(path):
    p = Path(path).resolve()
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)}


def local(root, value):
    p = (root / value).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('Project path must remain inside project: ' + str(value))
    return p


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()
