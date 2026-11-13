import json
def main(body: str, status_code: int) -> dict:
    try:
        data=json.loads(body)
        if int(status_code)!=200 or data.get('ok') is not True: raise ValueError()
        return {'ok':'yes','context':json.dumps(data,ensure_ascii=False),'answer':''}
    except (ValueError,TypeError):
        return {'ok':'no','context':'{}','answer':'这轮无法取得最新档案依据，可能有另一对话刚更新档案或记忆服务暂不可用。尚未提交变化，请重新发送。'}
