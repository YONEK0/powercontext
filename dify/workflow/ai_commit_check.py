import json
def main(body: str, status_code: int) -> dict:
    try:
        data=json.loads(body)
        if int(status_code)!=200 or data.get('ok') is not True or not isinstance(data.get('answer'),str): raise ValueError()
        if data.get('mutated') is True and not data.get('memory_id'): raise ValueError()
        return {'answer':data['answer']}
    except (ValueError,TypeError):
        return {'answer':'本轮操作未确认成功，不能宣布已归档、结案或准备完成。请重新查看档案或稍后重试。'}
