"""Deterministic case/checklist state, persisted as real PowerContext events.

Single bridge process: reads replay the user's event log; memory caches are not
authoritative. Each turn commits one bounded event. Old v2 endpoints remain intact.
"""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

TZ = timezone(timedelta(hours=8))
ROOT = Path(os.environ.get('BUREAU_DATA_DIR', '/data/bureau'))
TOKEN = os.environ.get('BUREAU_SERVICE_TOKEN', '')
NAMESPACE = os.environ.get('BUREAU_NAMESPACE', 'lostfound-v3')
LOCK = threading.RLock()
TURNS = {}
KINDS = {'record','find','continue','checked','clue','found','trip','trip_update','trip_status','status','recall','cancel','clarify','help'}


def stamp():
    return datetime.now(TZ).isoformat(timespec='seconds')


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def clean(value, limit=160):
    return str(value or '').strip()[:limit]


class Begin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(default='', max_length=256)
    query: str = Field(min_length=1, max_length=2000)


class Resolve(BaseModel):
    model_config = ConfigDict(extra='forbid')
    turn_id: str
    actions: list[dict] = Field(min_length=1, max_length=8)


class Commit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    turn_id: str
    plan: dict = Field(default_factory=dict)


class Clarify(Exception):
    pass


def fresh():
    return {'items': {}, 'cases': {}, 'trips': {}, 'pending': None,
            'last_item': None, 'last_case': None, 'last_trip': None, 'seq': 0}


def difference(before, after):
    patch = {}
    for key in ['items', 'cases', 'trips']:
        changed = {k: v for k, v in after[key].items() if before[key].get(k) != v}
        if changed:
            patch[key] = changed
    for key in ['pending', 'last_item', 'last_case', 'last_trip']:
        if before.get(key) != after.get(key):
            patch[key] = after[key]
    return patch


def apply_patch(state, patch):
    for key in ['items', 'cases', 'trips']:
        state[key].update(patch.get(key, {}))
    for key in ['pending', 'last_item', 'last_case', 'last_trip']:
        if key in patch:
            state[key] = patch[key]


class Store:
    def __init__(self, request, entries, artifact_id):
        self.request, self.entries, self.artifact_id = request, entries, artifact_id

    def scope(self, user):
        digest = hashlib.sha256((NAMESPACE + ':' + user).encode()).hexdigest()
        path = ROOT / (digest + '.json')
        if path.exists():
            return json.loads(path.read_text())['scope_id']
        result = self.request('POST', '/v1/scopes', {'title': '忘事管理局 · ' + digest[:12],
            'summary': '物品、寻物调查、出门检查的用户隔离档案', 'idempotency_key': 'lf3-' + digest})
        scope = result.get('scope_id')
        if not scope:
            raise HTTPException(502, 'PowerContext did not return a scope')
        ROOT.mkdir(parents=True, exist_ok=True)
        path.write_text(dump({'scope_id': scope}), encoding='utf-8')
        path.chmod(0o600)
        return scope

    def load(self, scope):
        state, events = fresh(), []
        for row in self.entries(scope):
            if row.get('kind') != 'bureau_event':
                continue
            try:
                event = json.loads(row['text'])
                if event.get('schema') != 'lostfound.event.v3':
                    continue
                event['memory_id'] = self.artifact_id(row)
                events.append(event)
            except (ValueError, KeyError):
                raise HTTPException(502, 'Invalid bureau event in PowerContext')
        events.sort(key=lambda e: e['seq'])
        seen = set()
        for event in events:
            if event['turn_id'] in seen:
                continue
            if event['seq'] != state['seq'] + 1:
                raise HTTPException(409, 'Event sequence conflict; state was not advanced')
            apply_patch(state, event['patch'])
            state['seq'] = event['seq']
            seen.add(event['turn_id'])
        return state, events

    def save(self, scope, before, after, turn):
        patch = difference(before, after)
        if not patch:
            return None
        event = {'schema': 'lostfound.event.v3', 'seq': before['seq'] + 1,
                 'turn_id': turn['id'], 'at': stamp(), 'query': turn['query'], 'patch': patch}
        text = dump(event)
        if len(text.encode()) > 7900:
            raise HTTPException(413, '本轮记录过多，请一次处理一件事；尚未保存。')
        try:
            result = self.request('POST', '/v1/memory/remember', {'scope_id': scope, 'kind': 'bureau_event', 'text': text})
            matched = [x for x in result.get('records', []) if x.get('text') == text and x.get('kind') == 'bureau_event']
            if not matched:
                raise HTTPException(502, 'PowerContext did not confirm the exact event')
            return self.artifact_id(matched[0])
        except HTTPException:
            # A timeout may follow an actual write: resolve by turn ID, never blindly append.
            _, events = self.load(scope)
            for item in events:
                if item['turn_id'] == turn['id']:
                    return item['memory_id']
            raise


def item(state, name, create=False):
    name = clean(name)
    if not name and state['last_item']:
        return state['items'][state['last_item']]
    matches = [x for x in state['items'].values() if name in [x['name']] + x['aliases']]
    if not matches and name:
        matches = [x for x in state['items'].values() if name in x['name'] or x['name'] in name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise Clarify('有多个相似物品：' + '、'.join(x['name'] for x in matches) + '。请说明完整名称。')
    if create and name:
        ident = 'item_' + secrets.token_hex(5)
        row = {'id': ident, 'name': name, 'aliases': [], 'location': None, 'usual_location': None,
               'condition': 'unknown', 'borrower': None, 'availability': 'unknown', 'updated_at': stamp()}
        state['items'][ident] = row
        return row
    raise Clarify('请说明是哪件物品；我还没有足够的物品记录。')


def active_case(state, turn, name=''):
    cases = [c for c in state['cases'].values() if c['status'] == 'open']
    if name:
        target = item(state, name)
        cases = [c for c in cases if c['item_id'] == target['id']]
    pending = state.get('pending') or {}
    if not name and len(cases) > 1 and pending.get('conversation_id') == turn['conversation_id']:
        cases = [c for c in cases if c['id'] == pending.get('case_id')] or cases
    if len(cases) != 1:
        if cases:
            names = '、'.join(state['items'][c['item_id']]['name'] for c in cases)
            raise Clarify('同时有多个未结案件：' + names + '。请说“继续找相机”或明确物品名称。')
        raise Clarify('目前没有对应的未结案件。请说“我的某件物品找不到了”开始调查。')
    return cases[0]


def active_trip(state, activity='', query=''):
    trips = list(state['trips'].values())
    if activity:
        trips = [t for t in trips if activity in t['activity'] or t['activity'] in activity]
    if re.search(r'20\d{2}-\d{2}-\d{2}|今天|明天|后天|下周|本周|这周|星期|周[一二三四五六日天]', query):
        trips = [t for t in trips if t['date'] == due_date(query)]
    if len(trips) > 1 and state['last_trip']:
        selected = next((t for t in trips if t['id'] == state['last_trip']),None)
        if selected:
            return selected
    if len(trips) == 1:
        return trips[0]
    raise Clarify('请先说明这次出门做什么，例如“明天出去拍照，帮我做出门检查”。')


def mentioned(value, turn, state):
    value = clean(value)
    if not value:
        return
    if value in turn['query']:
        return
    pending = state.get('pending') or {}
    if value in pending.get('locations', []):
        return
    raise Clarify('这处具体位置还没有得到确认，请直接说明位置名称。')


def pending(state, turn, case=None, kind='investigate', locations=None):
    state['pending'] = {'kind': kind, 'case_id': case['id'] if case else None,
        'conversation_id': turn['conversation_id'], 'locations': locations or [], 'at': stamp()}


def due_date(query):
    today = datetime.now(TZ).date()
    found = re.search(r'20\d{2}-\d{2}-\d{2}', query)
    if found:
        try:
            return datetime.fromisoformat(found.group()).date().isoformat()
        except ValueError:
            return None
    for word, offset in [('后天', 2), ('明天', 1), ('今天', 0)]:
        if word in query:
            return (today + timedelta(days=offset)).isoformat()
    found = re.search(r'(下周|本周|这周|周|星期)([一二三四五六日天])', query)
    if found:
        weekday = '一二三四五六日'.find(found[2].replace('天', '日'))
        monday = today - timedelta(days=today.weekday())
        result = monday + timedelta(days=weekday + (7 if found[1] == '下周' else 0))
        if result < today and found[1] not in ['本周', '这周']:
            result += timedelta(days=7)
        return result.isoformat()
    return today.isoformat()


def invalidate_pack(state, target):
    for trip in state['trips'].values():
        for check in trip['checks']:
            if check['item_id'] == target['id'] and check['task'] == '装包' and check['status'] == 'done':
                check['status'] = 'pending'
                check.pop('confirmed_at', None)
                check['updated_at'] = stamp()


def confirm_task(query, task, status):
    """A model extraction alone is not evidence that a task was completed."""
    if status == 'excluded':
        if not any(word in query for word in ['不带', '不用', '不需要', '不拿', '排除']):
            raise Clarify('请明确说本次不需要哪件物品，才能移出本次清单。')
        return
    positive = {'充电': r'(充好|充满|充完|充过|电量足|有电)',
                '取回': r'(取回|拿回|还了|还我|归还)',
                '装包': r'(装包|放进.{0,8}包|装进.{0,8}包|放包|带上|带好|装好)'}
    negative = {'充电': r'(没|未|还要|准备|打算|待|需要|要先).{0,3}充|没电|电量低',
                '取回': r'(没|未|还要|准备|打算|待|需要|要先).{0,3}(取回|拿回|归还|还我)',
                '装包': r'(没|未|别|不要|还要|准备|打算|待|需要|要先).{0,3}(装包|装进|放包|放进|带上|带好|装好)|从包里.{0,3}(拿|取)出来|拿出包|取出包'}
    if status == 'pending':
        if task not in negative or not re.search(negative[task], query):
            raise Clarify('请明确说明哪项尚未完成或需要重新准备。')
        return
    if status != 'done':
        raise Clarify('请说明这项是已完成、尚未完成，还是本次不需要。')
    if task not in positive or not re.search(positive[task], query) or re.search(negative[task], query):
        raise Clarify('这项还没有明确完成。请分别确认“电池充好了”“物品已取回”或“物品已装包”。')


def build_checks(state, trip, suggestions):
    names = suggestions or (['相机', '电池', '三脚架'] if any(x in trip['activity'] for x in ['拍照', '摄影']) else [])
    if not names:
        names = [x['name'] for x in list(state['items'].values())[:6]]
    for name in list(dict.fromkeys(clean(x, 40) for x in names if isinstance(x, str)))[:8]:
        try:
            target = item(state, name)
        except Clarify:
            target = None
        tasks = ['装包']
        if target and target['borrower']:
            tasks.insert(0, '取回')
        if target and (target['condition'] in ['needs_charge', 'charged'] or '电池' in target['name']):
            tasks.insert(0, '充电')
        for task in tasks:
            trip['checks'].append({'id': 'check_' + secrets.token_hex(4),
                'item_id': target['id'] if target else None, 'item': target['name'] if target else name,
                'task': task, 'status': 'pending', 'owned': target is not None})


def execute(state, turn, actions, plan):
    messages, focus = [], None
    for action in actions:
        kind = action.get('type')
        if kind not in KINDS:
            raise Clarify('请说明是登记位置、开始寻物，还是进行出门检查。')
        name = clean(action.get('item'))
        if kind == 'record':
            target = item(state, name, create=True)
            location = clean(action.get('location'))
            mode = action.get('location_kind', 'current')
            if mode not in ['current', 'usual', 'both']:
                raise Clarify('请说明这是当前位置、常用位置，还是两者都是。')
            if location:
                mentioned(location, turn, state)
                if mode in ['usual', 'both']:
                    target['usual_location'] = {'text': location, 'at': stamp()}
                if mode != 'usual':
                    target['location'] = {'text': location, 'at': stamp()}
                    target['availability'] = 'known'
                    invalidate_pack(state, target)
            condition = action.get('condition')
            if condition in ['charged', 'needs_charge']:
                if condition == 'charged':
                    confirm_task(turn['query'], '充电', 'done')
                elif not re.search(r'(没.{0,3}充|未充|待充|要充|需要充|没电|电量低)', turn['query']):
                    raise Clarify('请确认这件物品是否需要充电。')
                target['condition'] = condition
            borrower = clean(action.get('borrower'))
            if borrower:
                if borrower not in turn['query']:
                    raise Clarify('请确认借给了谁。')
                target['borrower'] = borrower
                target['availability'] = 'lent'
                invalidate_pack(state, target)
            if action.get('returned') is True:
                confirm_task(turn['query'], '取回', 'done')
                target['borrower'] = None
                target['availability'] = 'known'
            target['updated_at'] = stamp()
            state['last_item'] = target['id']
            messages.append('已更新物品档案：' + target['name'])
            focus = ('item', target['id'])
        elif kind == 'find':
            target = item(state, name, create=True)
            existing = [c for c in state['cases'].values() if c['item_id'] == target['id'] and c['status'] == 'open']
            case = existing[0] if existing else {'id': 'LF-' + secrets.token_hex(3).upper(),
                'item_id': target['id'], 'status': 'open', 'checked': [], 'clues': [],
                'opened_at': stamp(), 'found_location': None}
            state['cases'][case['id']] = case
            target['availability'] = 'missing'
            invalidate_pack(state, target)
            state['last_case'] = case['id']
            state['last_item'] = target['id']
            pending(state, turn, case)
            focus = ('case', case['id'])
        elif kind in ['continue', 'checked', 'clue', 'found', 'cancel']:
            case = active_case(state, turn, name)
            target = state['items'][case['item_id']]
            if kind == 'checked':
                places = action.get('locations') or (state.get('pending') or {}).get('locations', [])
                if not places:
                    raise Clarify('请告诉我已经检查过哪个位置，例如“蓝色背包找过了，没有”。')
                for place in places[:5]:
                    place = clean(place)
                    mentioned(place, turn, state)
                    if place not in case['checked']:
                        case['checked'].append(place)
                messages.append('已记录排查反馈。')
            if kind == 'clue':
                note = clean(action.get('note') or turn['query'], 450)
                if len(case['clues']) >= 10:
                    raise Clarify('本案已有较多线索，请先按现有线索排查或新开案件。')
                case['clues'].append({'text': note, 'at': stamp()})
                messages.append('已补充调查线索。')
            if kind == 'found':
                if not any(x in turn['query'] for x in ['找到了', '找到', '发现了', '寻回']) or re.search(r'(没|未|没有|还没)找到', turn['query']):
                    raise Clarify('请明确确认已经找到物品，案件才会结案。')
                place = clean(action.get('location'))
                mentioned(place, turn, state)
                case.update(status='closed', closed_at=stamp(), found_location=place or None)
                target['availability'] = 'known' if place else 'found'
                if place:
                    target['location'] = {'text': place, 'at': stamp()}
                pending(state, turn, case, 'storage', [place] if place else [])
                messages.append('本案已结，欢迎' + target['name'] + '归队。')
            elif kind == 'cancel':
                case['status'] = 'cancelled'
                state['pending'] = None
                messages.append('已取消本次调查，保留历史线索。')
            else:
                pending(state, turn, case)
            state['last_case'] = case['id']
            state['last_item'] = target['id']
            focus = ('case', case['id'])
        elif kind == 'trip':
            activity = clean(action.get('activity'), 80)
            if not activity:
                raise Clarify('这次出门做什么？例如拍照、运动或上班。')
            day = due_date(turn['query'])
            same = [t for t in state['trips'].values() if t['activity'] == activity and t['date'] == day]
            if same and not any(x in turn['query'] for x in ['重新', '新建', '另一次', '再来一次']):
                trip = same[-1]
            else:
                trip = {'id': 'TR-' + secrets.token_hex(3).upper(), 'activity': activity,
                        'date': day, 'checks': [], 'created_at': stamp()}
                requested = action.get('items') or []
                if not isinstance(requested, list) or any(not isinstance(x,str) or x not in turn['query'] for x in requested):
                    raise Clarify('请直接列出这次要核对的物品名称。')
                build_checks(state, trip, requested or plan.get('checklist', []))
                state['trips'][trip['id']] = trip
            state['last_trip'] = trip['id']
            state['pending'] = None
            focus = ('trip', trip['id'])
        elif kind in ['trip_update', 'trip_status']:
            trip = active_trip(state, clean(action.get('activity'), 80), turn['query'])
            if kind == 'trip_update':
                selected = [x for x in trip['checks'] if name and (name in x['item'] or x['item'] in name)]
                if not selected:
                    raise Clarify('请说明要更新清单中的哪件物品。')
                status = action.get('status', 'done')
                task = action.get('task', '装包')
                confirm_task(turn['query'], task, status)
                selected = [x for x in selected if status == 'excluded' or x['task'] == task]
                if not selected:
                    raise Clarify('这件物品没有对应检查项，请说明是充电、取回还是装包。')
                for check in selected:
                    target = state['items'].get(check['item_id'])
                    if status == 'done' and target and target['borrower'] and task == '装包':
                        raise Clarify(target['name'] + '仍有借出记录，请先确认是否已经取回。')
                    if status == 'done' and target and target['availability'] == 'missing' and task == '装包':
                        raise Clarify(target['name'] + '仍有未结寻物案，请先确认是否已经找到。')
                    check['status'] = status
                    check['updated_at'] = stamp()
                    if status == 'pending':
                        check.pop('confirmed_at', None)
                    else:
                        check['confirmed_at'] = stamp()
                    if target and task == '充电' and status != 'excluded':
                        target['condition'] = 'charged' if status == 'done' else 'needs_charge'
                    if target and task == '取回' and status == 'done':
                        target['borrower'] = None
                        target['availability'] = 'known'
                messages.append('已更新本次出门清单：' + name)
            state['last_trip'] = trip['id']
            focus = ('trip', trip['id'])
        elif kind == 'recall':
            target = item(state, name)
            focus = ('item', target['id'])
        elif kind == 'status':
            focus = ('overview', None)
        elif kind == 'clarify':
            cases = [c for c in state['cases'].values() if c['status'] == 'open']
            if len(cases) > 1 and any(x in turn['query'] for x in ['继续', '办案', '没找到', '找到了']):
                raise Clarify('同时有多个未结案件：' + '、'.join(state['items'][c['item_id']]['name'] for c in cases) + '。请明确本轮要继续哪件物品。')
            raise Clarify('请补充具体物品或活动，例如“相机找不到了”或“明天拍照，做出门检查”。')
        else:
            focus = ('help', None)
    return messages, focus


def render(state, focus, messages, plan, turn):
    lines = list(dict.fromkeys(messages))
    if focus and focus[0] == 'case':
        case = state['cases'][focus[1]]
        target = state['items'][case['item_id']]
        label = {'open': '调查中', 'closed': '已结案', 'cancelled': '已取消'}[case['status']]
        lines += ['\n### 寻物案 ' + case['id'] + '｜' + label, '**物品：** ' + target['name']]
        if case['status'] == 'closed':
            lines.append('**本次找到：** ' + (case['found_location'] or '用户已确认找到，具体位置未登记'))
            lines.append('找到的位置不会自动变成常用位置。你现在把它放在哪里？需要设为常用位置吗？')
        elif case['status'] == 'open':
            if target['location']:
                lines.append('**上次位置：** ' + target['location']['text'] + '（' + target['location']['at'][:16].replace('T',' ') + ' 的记录，当前位置待确认）')
            else:
                lines.append('**历史位置：** 暂无记录，需要你补充线索。')
            lines.append('**已排查：** ' + ('、'.join(case['checked']) or '暂无'))
            if case['clues']:
                lines.append('**近期线索：** ' + '；'.join(x['text'] for x in case['clues'][-3:]))
            suggestion = clean(plan.get('suggestion'), 220)
            question = clean(plan.get('question'), 180)
            place = clean(plan.get('next_location'))
            grounding = turn['query'] + dump(target) + dump(case['clues'])
            if place and place not in grounding:
                suggestion, question, place = '', '', ''
            if place and any(place in p or p in place for p in case['checked']):
                suggestion, question, place = '', '', ''
            if not place and any(p in suggestion + question for p in case['checked']):
                suggestion, question = '', ''
            if not suggestion and not case['checked'] and target['location']:
                place = target['location']['text']
                suggestion = '先检查上次登记的位置：' + place
                question = '这里检查过了吗？你可以回复“找到了”或“没有”。'
            if not question:
                question = '最后一次看到或使用它是在什么时候、什么场景？' if not case['clues'] else '还有哪个相关位置没有检查过？请告诉我新的排查结果。'
            if suggestion:
                lines.append('**下一步建议（未确认）：** ' + suggestion)
            lines.append('**继续调查：** ' + question)
            pending(state, turn, case, locations=[place] if place else [])
    elif focus and focus[0] == 'item':
        target = state['items'][focus[1]]
        lines += ['\n### 物品档案｜' + target['name'],
                  '上次位置：' + (target['location']['text'] if target['location'] else '未登记'),
                  '常用位置：' + (target['usual_location']['text'] if target['usual_location'] else '未设置')]
        if target['borrower']:
            lines.append('借出给：' + target['borrower'])
        if target['condition'] != 'unknown':
            lines.append('最近报告状态：' + {'charged': '已充电', 'needs_charge': '待充电'}[target['condition']] + '；出门前仍需确认本次准备情况。')
    elif focus and focus[0] == 'trip':
        trip = state['trips'][focus[1]]
        lines += ['\n### 出门检查｜' + trip['activity'] + '｜' + trip['date']]
        table = ['| 物品 | 本次事项 | 状态 | 已有记录 |', '|---|---|---|---|']
        for check in trip['checks']:
            target = state['items'].get(check['item_id'])
            evidence = '建议项；尚无物品档案'
            if target:
                evidence = '上次位置：' + (target['location']['text'] if target['location'] else '未登记')
                if target['borrower']:
                    evidence = '仍借给' + target['borrower']
                if target['availability'] == 'missing':
                    evidence = '仍有未结寻物案'
            status = {'pending': '待处理', 'done': '已确认', 'excluded': '本次不需要'}[check['status']]
            safe = lambda text: str(text).replace('|','／').replace('\n',' ')
            table.append('| ' + ' | '.join(map(safe,[check['item'],check['task'],status,evidence])) + ' |')
        lines.append('\n'.join(table))
        remaining = [x for x in trip['checks'] if x['status'] == 'pending']
        lines.append('**还差：** ' + ('、'.join(x['item'] + '·' + x['task'] for x in remaining) if remaining else '本次清单已逐项确认；出发前请核对实际携带情况。'))
        lines.append('可以回复“电池充好了”“相机已装包”或“这次不带三脚架”。充好电不会自动标记已装包。')
    elif focus and focus[0] == 'overview':
        cases = [c for c in state['cases'].values() if c['status'] == 'open']
        lines.append('未结寻物案：' + ('、'.join(state['items'][c['item_id']]['name'] + '（' + c['id'] + '）' for c in cases) or '暂无'))
        if state['last_trip']:
            lines.append('最近出门清单：' + state['trips'][state['last_trip']]['activity'])
    else:
        lines.append('这里是忘事管理局，值班员阿档为你办案。\n\n告诉我“相机放蓝色背包了”登记位置；说“相机找不到了”启动调查；说“明天拍照，做出门检查”生成准备清单。新对话可以继续案件。')
    return '\n\n'.join(lines)


def install_bureau(app, request, entries, artifact_id):
    store = Store(request, entries, artifact_id)

    def authorize(value):
        if not TOKEN or not hmac.compare_digest(value, 'Bearer ' + TOKEN):
            raise HTTPException(401, 'Invalid internal service credential')

    def turn_for(ident):
        turn = TURNS.get(ident)
        if not turn or time.monotonic() - turn['created'] > 900:
            raise HTTPException(409, 'This turn expired; please send the message again')
        return turn

    @app.post('/v2/turns/begin')
    def begin(body: Begin, authorization: str = Header(default='')):
        authorize(authorization)
        with LOCK:
            for key in [k for k,v in TURNS.items() if time.monotonic()-v['created'] > 900]:
                TURNS.pop(key, None)
            scope = store.scope(body.user_id)
            state, events = store.load(scope)
            ident = secrets.token_hex(16)
            turn = {'id': ident, **body.model_dump(), 'scope': scope, 'seq': state['seq'], 'created': time.monotonic()}
            TURNS[ident] = turn
            context = {'today': datetime.now(TZ).date().isoformat(), 'timezone': 'Asia/Shanghai',
                'new_conversation': bool(state.get('pending') and state['pending']['conversation_id'] != body.conversation_id),
                'state': state, 'recent_user_inputs': [e['query'] for e in events[-3:]],
                'evidence_ids': [e['memory_id'] for e in events[-8:]]}
            # Bound the prompt without silently truncating JSON or corrupting state.
            if len(dump(context).encode()) > 24000:
                raise HTTPException(413, '档案较多，请先使用具体物品名称；本演示版本的上下文预算已满。')
            return {'ok': True, 'turn_id': ident, 'context': context}

    @app.post('/v2/context/resolve')
    def resolve(body: Resolve, authorization: str = Header(default='')):
        authorize(authorization)
        with LOCK:
            turn = turn_for(body.turn_id)
            if any(a.get('type') not in KINDS for a in body.actions):
                raise HTTPException(422, 'Unsupported action')
            state, events = store.load(turn['scope'])
            if state['seq'] != turn['seq']:
                raise HTTPException(409, 'Another conversation updated the archive; retry with fresh state')
            turn['actions'] = body.actions
            # Search is real; replayed current state prevents stale search hits becoming current facts.
            hits = request('POST', '/v1/memory/search', {'scope_id': turn['scope'], 'query': turn['query'], 'limit': 5, 'mode': 'fts'})
            if not isinstance(hits.get('hits'), list):
                raise HTTPException(502, 'Invalid search response')
            evidence = [{'id': e['memory_id'], 'query': e['query'], 'at': e['at']} for e in events[-8:]]
            return {'ok': True, 'turn_id': turn['id'], 'actions': body.actions, 'state': state,
                    'query': turn['query'], 'evidence': evidence, 'retrieval_hits': len(hits['hits'])}

    @app.post('/v2/turns/commit')
    def commit(body: Commit, authorization: str = Header(default='')):
        authorize(authorization)
        with LOCK:
            turn = turn_for(body.turn_id)
            if 'result' in turn:
                return {**turn['result'], 'duplicate': True}
            if 'actions' not in turn:
                raise HTTPException(409, 'Resolve this turn before committing')
            state, events = store.load(turn['scope'])
            prior = next((e for e in events if e['turn_id'] == turn['id']), None)
            if prior:
                raise HTTPException(409, 'This turn was already saved; reopen the current archive')
            if state['seq'] != turn['seq']:
                raise HTTPException(409, 'Another conversation changed this archive; retry')
            after = copy.deepcopy(state)
            allowed_ids = {e['memory_id'] for e in events[-8:]}
            plan = body.plan if isinstance(body.plan, dict) else {}
            refs = plan.get('evidence_ids', [])
            if not isinstance(refs, list) or any(x not in allowed_ids for x in refs):
                plan = {}
            try:
                messages, focus = execute(after, turn, turn['actions'], plan)
                answer = render(after, focus, messages, plan, turn)
            except Clarify as error:
                after, answer = copy.deepcopy(state), str(error)
                focus = ('clarify', None)
            receipt = store.save(turn['scope'], state, after, turn)
            updated = state['seq'] + bool(receipt)
            if receipt:
                answer += '\n\n— 档案已同步 · 版本 ' + str(updated) + ' · 换新对话可继续'
            result = {'ok': True, 'answer': answer, 'mutated': bool(receipt), 'memory_id': receipt,
                      'scope_id': turn['scope'], 'version': updated, 'focus': focus,
                      'state': {**after, 'seq': updated}, 'evidence_ids': [e['memory_id'] for e in events[-8:]], 'duplicate': False}
            turn['result'] = result
            return result
