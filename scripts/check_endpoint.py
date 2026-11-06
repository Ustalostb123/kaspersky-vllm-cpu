import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

url = 'http://127.0.0.1:8000'
token = os.environ['API_TOKEN']
marker = 'check_' + uuid.uuid4().hex

try:
    urllib.request.urlopen(url + '/v1/models', timeout=30)
    raise RuntimeError('Request without a token was accepted')
except urllib.error.HTTPError as error:
    assert error.code == 401

body = json.dumps({'model': 'local-instruct',
                   'messages': [{'role': 'user', 'content': 'Repeat this code: ' + marker}],
                   'max_tokens': 48, 'temperature': 0}).encode()
request = urllib.request.Request(url + '/v1/chat/completions', data=body,
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + token})
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)
answer = result['choices'][0]['message']['content']
assert answer.strip()
for attempt in range(20):
    logs = subprocess.check_output(['kubectl', '-n', 'llm-cpu', 'logs',
                                    'deployment/inference', '-c', 'server'], text=True)
    if 'request_completed' in logs and '"status": 200' in logs:
        break
    time.sleep(0.25)
else:
    raise RuntimeError('Request event is missing from logs')
assert marker not in logs
assert token not in logs
if len(answer.strip()) > 10:
    assert answer.strip() not in logs
print('Authorization, generation and request logs: OK')
