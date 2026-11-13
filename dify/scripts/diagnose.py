"""Read-only readiness checks; failures exit nonzero."""
import argparse
import json
from pathlib import Path
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default='http://127.0.0.1:18081')
parser.add_argument('--bridge-url', default='http://127.0.0.1:19080')
args = parser.parse_args()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for label, url in [('Dify', args.base_url + '/console/api/setup'), ('Memory Bridge + PowerContext', args.bridge_url + '/health')]:
    with opener.open(url, timeout=20) as response:
        data = json.loads(response.read())
        assert response.status == 200
    if label == 'Dify':
        assert data.get('step') == 'finished', data
    else:
        assert data.get('ok') is True and isinstance(data.get('memories'), int), data
    print(label, 'OK', json.dumps(data, ensure_ascii=False))
connection = Path(__file__).resolve().parents[1] / '.runtime/connection.json'
if connection.exists():
    settings = json.loads(connection.read_text(encoding='utf-8'))
    with opener.open(settings['webapp_url'], timeout=15) as response:
        assert response.status == 200
    print('Published WebApp OK:', settings['webapp_url'])
