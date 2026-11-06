import asyncio
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from privacy import AuditMiddleware, SafeFormatter


class PrivacyTests(unittest.TestCase):
    def test_exception_and_message_are_not_formatted(self):
        record = logging.LogRecord('vllm', logging.ERROR, '', 0,
                                   'PRIVATE_PROMPT %s', ('PRIVATE_RESPONSE',), None)
        try:
            raise ValueError('PRIVATE_EXCEPTION')
        except ValueError:
            record.exc_info = sys.exc_info()
        record.stack_info = 'PRIVATE_STACK'
        result = SafeFormatter().format(record)
        self.assertNotIn('PRIVATE', result)
        self.assertEqual(json.loads(result)['event'], 'runtime_log')

    def test_stream_auth_and_failure(self):
        async def run(token, failure=False):
            messages = []
            async def send(message):
                messages.append(message)
            async def app(scope, receive, send):
                if failure:
                    raise ValueError('PRIVATE_PROMPT')
                await send({'type': 'http.response.start', 'status': 200})
                await send({'type': 'http.response.body', 'body': b'PRIVATE_RESPONSE', 'more_body': True})
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            output = io.StringIO()
            logger = logging.getLogger('audit')
            handler = logging.StreamHandler(output)
            handler.setFormatter(SafeFormatter())
            with patch.dict(os.environ, {'VLLM_API_KEY': 'x' * 32}):
                middleware = AuditMiddleware(app)
            with patch.object(logger, 'handlers', [handler]), patch.object(logger, 'level', logging.INFO):
                try:
                    await middleware({'type': 'http', 'path': '/v1/chat/completions',
                                      'headers': [(b'authorization', token)]}, None, send)
                except ValueError:
                    pass
            self.assertNotIn('PRIVATE', output.getvalue())
            event = json.loads(output.getvalue())
            self.assertEqual(event['event'], 'request_completed')
            return event['status'], messages
        status, messages = asyncio.run(run(b'Bearer ' + b'x' * 32))
        self.assertEqual(status, 200)
        self.assertEqual(len(messages), 3)
        status, _ = asyncio.run(run(b'wrong'))
        self.assertEqual(status, 401)
        status, _ = asyncio.run(run(b'Bearer ' + b'x' * 32, failure=True))
        self.assertEqual(status, 500)

    def test_verified_warm_cache_does_not_import_hf(self):

        stub = types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=2)
        with patch.dict(sys.modules, {'fcntl': stub}):
            spec = importlib.util.spec_from_file_location('download_model', ROOT / 'app/download_model.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        revision = 'a' * 40
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as tmp:
            target = Path(tmp) / revision
            target.mkdir()
            data = b'model-data'
            (target / 'model.safetensors').write_bytes(data)
            (target / '.complete.json').write_text(json.dumps({
                'model_id': 'test/model', 'revision': revision,
                'files': {'model.safetensors': hashlib.sha256(data).hexdigest()}}))
            self.assertTrue(module.valid_cache(target, 'test/model', revision))
            with patch.dict(os.environ, {'MODEL_ROOT': tmp, 'MODEL_ID': 'test/model', 'MODEL_REVISION': revision}), \
                 patch.dict(sys.modules, {'huggingface_hub': None}):
                module.main()
            (target / 'model.safetensors').write_bytes(b'corrupt')
            self.assertFalse(module.valid_cache(target, 'test/model', revision))


if __name__ == '__main__':
    unittest.main()
