"""Generate instance-local secrets; never include .env in a submission archive."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / '.env'
if path.exists():
    existing = path.read_text(encoding='utf-8')
    extra = {'BUREAU_SERVICE_TOKEN': secrets.token_urlsafe(40),
             'BUREAU_NAMESPACE': 'lostfound-v3', 'DEEPSEEK_MODEL': 'deepseek-flash'}
    missing = {k:v for k,v in extra.items() if not any(line.startswith(k+'=') for line in existing.splitlines())}
    if missing:
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write('\n' + '\n'.join(f'{k}={v}' for k,v in missing.items()) + '\n')
    path.chmod(0o600)
    print('Preserved existing .env; initialized any missing AI settings (secrets hidden)')
    raise SystemExit(0)
values = {
    'SECRET_KEY': secrets.token_urlsafe(48),
    'DB_PASSWORD': secrets.token_hex(24),
    'DB_USERNAME': 'postgres',
    'DB_DATABASE': 'dify',
    'POSTGRES_PASSWORD': '',
    'REDIS_PASSWORD': secrets.token_hex(24),
    'SANDBOX_API_KEY': secrets.token_hex(24),
    'CODE_EXECUTION_API_KEY': '',
    'EXPOSE_NGINX_PORT': '127.0.0.1:18081',
    'EXPOSE_NGINX_SSL_PORT': '127.0.0.1:18443',
    'BRIDGE_PORT': '19080',
    'VECTOR_STORE': 'weaviate',
    'CERTBOT_EMAIL': '',
    'CERTBOT_DOMAIN': '',
    'CHECK_UPDATE_URL': '',
    'DIFY_WEB_API_CORS_ALLOW_ORIGINS': '*',
    'BUREAU_SERVICE_TOKEN': secrets.token_urlsafe(40),
    'BUREAU_NAMESPACE': 'lostfound-v3',
    'DEEPSEEK_MODEL': 'deepseek-flash',
}
values['POSTGRES_PASSWORD'] = values['DB_PASSWORD']
values['CODE_EXECUTION_API_KEY'] = values['SANDBOX_API_KEY']
with path.open('x', encoding='utf-8', newline='\n') as stream:
    stream.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
path.chmod(0o600)
print('Created instance configuration: .env (secrets hidden)')
