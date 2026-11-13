import json
def main(query: str, user_id: str, conversation_id: str) -> dict:
    return {'payload':json.dumps({'query':query,'user_id':user_id,'conversation_id':conversation_id},ensure_ascii=False)}
