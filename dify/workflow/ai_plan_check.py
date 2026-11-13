import json
def main(text: str, turn_id: str) -> dict:
    try:
        raw=text.strip()
        if raw.startswith('```'): raw=raw.split('\n',1)[1].rsplit('```',1)[0]
        data=json.loads(raw)
        if not isinstance(data,dict): raise ValueError()
        plan={k:data[k] for k in ['suggestion','question','next_location','checklist','evidence_ids'] if k in data}
        for key in ['suggestion','question','next_location']:
            if key in plan and (not isinstance(plan[key],str) or len(plan[key])>500): raise ValueError()
        for key in ['checklist','evidence_ids']:
            if key in plan and (not isinstance(plan[key],list) or len(plan[key])>10 or any(not isinstance(x,str) or len(x)>180 for x in plan[key])): raise ValueError()
    except (ValueError,TypeError,IndexError): plan={}
    return {'payload':json.dumps({'turn_id':turn_id,'plan':plan},ensure_ascii=False)}
