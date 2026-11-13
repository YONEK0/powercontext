"""Set up the dedicated local Dify instance, import DSL, publish, and create a test API key."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(base, path, payload=None, token=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=90) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        message = error.read().decode(errors='replace')[:800]
        key = os.environ.get('DEEPSEEK_API_KEY', '')
        if key:
            message = message.replace(key, '[REDACTED]')
        raise RuntimeError(f'{path}: HTTP {error.code}: {message}') from None


def save_private(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:18081')
    parser.add_argument('--wait', type=int, default=300)
    parser.add_argument('--key-stdin', action='store_true', help='Read the DeepSeek key from stdin, never a command argument')
    args = parser.parse_args()
    if args.key_stdin:
        value = sys.stdin.read().strip()
        if not value or '\n' in value:
            raise RuntimeError('Expected one DeepSeek key on stdin')
        os.environ['DEEPSEEK_API_KEY'] = value
    base = args.base_url.rstrip('/')
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    deadline = time.monotonic() + args.wait
    while True:
        try:
            setup = api(base, '/console/api/setup')
            break
        except (OSError, RuntimeError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Dify is not ready; run bash scripts/compose.sh logs --tail=80 api nginx db')
            time.sleep(2)
    admin_path = RUNTIME / 'admin.json'
    if admin_path.exists():
        admin = json.loads(admin_path.read_text(encoding='utf-8'))
    elif setup.get('step') != 'finished':
        admin = {'email': os.environ.get('DIFY_ADMIN_EMAIL', 'lostfound-admin@example.com'),
                 'password': os.environ.get('DIFY_ADMIN_PASSWORD') or ('Lf9' + secrets.token_urlsafe(24)),
                 'name': 'LostFound Admin'}
        save_private(admin_path, admin)
    elif os.environ.get('DIFY_ADMIN_EMAIL') and os.environ.get('DIFY_ADMIN_PASSWORD'):
        admin = {'email': os.environ['DIFY_ADMIN_EMAIL'], 'password': os.environ['DIFY_ADMIN_PASSWORD'], 'name': 'LostFound Admin'}
    else:
        raise RuntimeError('This Dify instance is already initialized. Set DIFY_ADMIN_EMAIL and DIFY_ADMIN_PASSWORD to its existing credentials.')
    if setup.get('step') != 'finished':
        api(base, '/console/api/setup', admin)
        print('Initialized dedicated Dify administrator (credentials saved locally, hidden).', flush=True)
    login = api(base, '/console/api/login', {**admin, 'language': 'zh-Hans', 'remember_me': True})
    token = login.get('data', {}).get('access_token')
    if not token:
        raise RuntimeError('Dify did not return an administrator access token')
    settings = dict(line.split('=',1) for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines() if '=' in line and not line.startswith('#'))
    model = settings.get('DEEPSEEK_MODEL', 'deepseek-flash')
    if model != 'deepseek-flash':
        raise RuntimeError('This release is validated with deepseek-flash. Regenerate and validate the DSL before changing the model.')
    model_path = '/console/api/workspaces/current/model-providers/openai_api_compatible/models'
    deepseek_key = os.environ.get('DEEPSEEK_API_KEY')
    if deepseek_key:
        credentials = {'api_key': deepseek_key, 'endpoint_url': 'http://memory-bridge:19080/model/v1',
            'mode': 'chat', 'context_size': '32768', 'max_tokens_to_sample': '1800',
            'function_calling_type': 'no_call', 'stream_function_calling': 'not_supported',
            'vision_support': 'no_support', 'stream_mode_delimiter': r'\n\n'}
        api(base, model_path, {'model':model, 'model_type':'llm', 'credentials':credentials}, token)
        print('Validated and encrypted the local DeepSeek key in Dify. Model: ' + model, flush=True)
    else:
        models = api(base, model_path, token=token).get('data', [])
        if not any(row.get('model') == model for row in models):
            raise RuntimeError('Set DEEPSEEK_API_KEY in this terminal, or configure deepseek-flash in Dify Settings > Model Provider first.')
    connection_path = RUNTIME / 'connection.json'
    old = json.loads(connection_path.read_text(encoding='utf-8')) if connection_path.exists() else {}
    service_token = settings.get('BUREAU_SERVICE_TOKEN')
    if not service_token:
        raise RuntimeError('Run python3 scripts/init_env.py before importing the AI workflow')
    dsl = (ROOT / 'LostFound.yml').read_text(encoding='utf-8').replace('__SET_BY_BOOTSTRAP__', service_token)
    payload = {'mode': 'yaml-content', 'yaml_content': dsl}
    if old.get('app_id'):
        payload['app_id'] = old['app_id']
    imported = api(base, '/console/api/apps/imports', payload, token)
    if imported.get('status') == 'pending':
        imported = api(base, '/console/api/apps/imports/' + imported['id'] + '/confirm', {}, token)
    app_id = imported.get('app_id')
    if not app_id:
        raise RuntimeError('Dify import did not return an app ID: ' + str(imported))
    api(base, f'/console/api/apps/{app_id}/workflows/publish', {}, token)
    api(base, f'/console/api/apps/{app_id}/site', {'default_language': 'zh-Hans', 'show_workflow_steps': False}, token)
    detail = api(base, f'/console/api/apps/{app_id}', token=token)
    if not detail.get('enable_api') or not detail.get('enable_site'):
        raise RuntimeError('Imported application does not have API and WebApp enabled')
    keys = api(base, f'/console/api/apps/{app_id}/api-keys', token=token)['data']
    key = keys[0]['token'] if keys else api(base, f'/console/api/apps/{app_id}/api-keys', {}, token)['token']
    code = detail['site']['code']
    connection = {'base_url': base, 'app_id': app_id, 'site_code': code, 'webapp_url': base + '/chat/' + code, 'api_key': key}
    save_private(connection_path, connection)
    print('Imported and published LostFound.yml. App ID:', app_id, flush=True)
    print('WebApp:', connection['webapp_url'], flush=True)
    print('Local administrator credentials:', admin_path, flush=True)
    print('Local API configuration:', connection_path, '(API key hidden)', flush=True)


if __name__ == '__main__':
    main()
