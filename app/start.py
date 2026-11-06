import glob
import json
import os
from pathlib import Path
import sys


def main():
    config = json.loads(Path('/config/serve.json').read_text())
    allowed = {'dtype', 'max-model-len', 'max-num-seqs', 'max-num-batched-tokens',
               'enable-prefix-caching', 'enable-chunked-prefill', 'enforce-eager'}
    if set(config) - allowed:
        raise ValueError('Unsupported configuration keys')
    if len(os.environ.get('VLLM_API_KEY', '')) < 32:
        raise ValueError('API key is missing or too short')
    model = Path('/models') / os.environ['MODEL_REVISION']
    if not (model / '.complete.json').is_file():
        raise RuntimeError('Model download is incomplete')

    libraries = glob.glob(sys.prefix + '/**/libiomp5.so', recursive=True)
    if not libraries:
        raise RuntimeError('Intel OpenMP library not found')
    os.environ['LD_PRELOAD'] = libraries[0]
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      VLLM_NO_USAGE_STATS='1', DO_NOT_TRACK='1',
                      VLLM_LOGGING_CONFIG_PATH='/config/logging.json')
    args = [sys.executable, '-m', 'vllm.entrypoints.openai.api_server',
            '--host', '0.0.0.0', '--port', '8000', '--model', str(model),
            '--served-model-name', 'local-instruct',
            '--middleware', 'privacy.AuditMiddleware',
            '--log-config-file', '/config/logging.json', '--disable-uvicorn-access-log']


    for name, value in config.items():
        if isinstance(value, bool):
            args.append('--' + ('' if value else 'no-') + name)
        else:
            args.extend(['--' + name, str(value)])
    os.execv(sys.executable, args)


if __name__ == '__main__':
    main()
