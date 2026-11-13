import json
def main(text: str, turn_id: str) -> dict:
    allowed={'record','find','continue','checked','clue','found','trip','trip_update','trip_status','status','recall','cancel','clarify','help'}
    fields={'type','item','items','location','location_kind','condition','borrower','returned','locations','note','activity','task','status'}
    try:
        raw=text.strip()
        if raw.startswith('```'): raw=raw.split('\n',1)[1].rsplit('```',1)[0]
        actions=json.loads(raw)['actions']
        if not isinstance(actions,list) or not 1<=len(actions)<=8: raise ValueError()
        for action in actions:
            if not isinstance(action,dict) or action.get('type') not in allowed or set(action)-fields: raise ValueError()
            for key,value in action.items():
                if key=='returned':
                    if not isinstance(value,bool): raise ValueError()
                elif key in ['locations','items']:
                    if not isinstance(value,list) or len(value)>8 or any(not isinstance(x,str) or len(x)>160 for x in value): raise ValueError()
                elif not isinstance(value,str) or len(value)>500: raise ValueError()
        return {'ok':'yes','payload':json.dumps({'turn_id':turn_id,'actions':actions},ensure_ascii=False),'answer':''}
    except (ValueError,TypeError,KeyError,IndexError):
        return {'ok':'no','payload':'{}','answer':'模型这轮没有返回可执行的理解结果，档案尚未修改。请明确说明，例如“相机找不到了”。'}
