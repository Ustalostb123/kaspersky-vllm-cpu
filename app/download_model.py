import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def valid_cache(target, model_id, revision):
    try:
        manifest = json.loads((target / '.complete.json').read_text())
        if manifest['model_id'] != model_id or manifest['revision'] != revision:
            return False
        files = manifest['files']
        return bool(files) and all(
            (target / name).is_file() and digest(target / name) == checksum
            for name, checksum in files.items())
    except (OSError, ValueError, KeyError):
        return False


def main():
    model_id = os.environ['MODEL_ID']
    revision = os.environ['MODEL_REVISION']
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('MODEL_REVISION must be a full commit hash')
    root = Path(os.environ.get('MODEL_ROOT', '/models'))
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = root / revision
        if valid_cache(target, model_id, revision):
            print('{"event":"model_cache_hit"}', flush=True)
            return
        staging = root / (revision + '.partial')
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=model_id, revision=revision, local_dir=staging,
                          token=os.environ.get('HF_TOKEN') or None,
                          allow_patterns=['*.json', '*.safetensors', '*.model',
                                          'merges.txt', 'vocab.txt', '*.jinja'],
                          max_workers=2)
        if not (staging / 'config.json').is_file() or not list(staging.glob('*.safetensors')):
            raise RuntimeError('Required model files are missing')
        files = {str(p.relative_to(staging)): digest(p) for p in staging.rglob('*')
                 if p.is_file() and '.cache' not in p.relative_to(staging).parts}
        (staging / '.complete.json').write_text(json.dumps(
            {'model_id': model_id, 'revision': revision, 'files': files}))
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
        print('{"event":"model_download_complete"}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:

        print('{"event":"model_download_failed"}', file=sys.stderr)
        sys.exit(1)
