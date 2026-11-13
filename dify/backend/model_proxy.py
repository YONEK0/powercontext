"""Thin DeepSeek transport adapter for Dify 0.15.3; no agent or business logic."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()
MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
AUDIT = Path(os.environ.get('MODEL_AUDIT_FILE', '/data/model-usage.jsonl'))
AUDIT_LOCK = threading.Lock()
OPENER = urllib.request.build_opener()


@router.post('/model/v1/chat/completions')
def complete(payload: dict, authorization: str = Header(default='')):
    if not authorization.startswith('Bearer ') or len(authorization) < 20:
        raise HTTPException(401, 'A DeepSeek API Key is required')
    if payload.get('model') != MODEL:
        raise HTTPException(400, 'Model does not match the configured DeepSeek model')
    messages = payload.get('messages', [])
    if not isinstance(messages, list) or not messages or len(messages) > 20:
        raise HTTPException(400, 'Invalid messages')
    if len(json.dumps(messages, ensure_ascii=False).encode()) > 65000:
        raise HTTPException(413, 'Context exceeds application budget')
    messages = [{'role': 'system', 'content': 'Return only a valid JSON object. Do not output reasoning or markdown fences.'}] + messages
    body = {
        'model': MODEL, 'messages': messages, 'stream': False,
        'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'},
        'max_tokens': max(64, min(int(payload.get('max_tokens', 1200)), 1800)),
        'temperature': max(0, min(float(payload.get('temperature', 0.1)), 0.7)),
    }
    # The destination cannot be supplied by a prompt or by an API caller.
    req = urllib.request.Request('https://api.deepseek.com/v1/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={'Authorization': authorization, 'Content-Type': 'application/json'})
    started = time.monotonic()
    try:
        with OPENER.open(req, timeout=50) as response:
            result = json.load(response)
        content = result['choices'][0]['message'].get('content')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Empty completion')
    except urllib.error.HTTPError as error:
        # Do not return upstream bodies, request headers, or credentials.
        raise HTTPException(502, f'DeepSeek returned HTTP {error.code}') from None
    except (OSError, ValueError, KeyError, IndexError):
        raise HTTPException(503, 'DeepSeek timed out or returned an invalid response') from None
    record = {'at': datetime.now(timezone.utc).isoformat(), 'provider': 'deepseek',
              'model': result.get('model'), 'request_id': result.get('id'),
              'usage': result.get('usage', {}), 'latency_ms': round((time.monotonic()-started)*1000),
              'thinking': 'disabled', 'status': 200}
    with AUDIT_LOCK:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    if not payload.get('stream'):
        return result
    # Dify asks for SSE internally; the two private planning nodes are not streamed to users.
    base = {k: result.get(k) for k in ['id', 'created', 'model']}
    base['object'] = 'chat.completion.chunk'
    first = {**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}]}
    last = {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': result['choices'][0].get('finish_reason', 'stop')}], 'usage': result.get('usage')}
    chunks = ['data: ' + json.dumps(x, ensure_ascii=False) + '\n\n' for x in [first, last]] + ['data: [DONE]\n\n']
    return StreamingResponse(iter(chunks), media_type='text/event-stream')
