import json
def main(body: str, status_code: int) -> dict:
    try:
        data=json.loads(body)
        if int(status_code)!=200 or data.get('ok') is not True or not data.get('turn_id'): raise ValueError()
        return {'ok':'yes','turn_id':data['turn_id'],'context':json.dumps(data['context'],ensure_ascii=False),'answer':''}
    except (ValueError,TypeError,KeyError):
        return {'ok':'no','turn_id':'','context':'{}','answer':'档案服务暂时无法读取，这轮没有开始登记或办案。请稍后再试。'}
