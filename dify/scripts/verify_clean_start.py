"""Short live smoke suite for an independently initialized deployment (paid)."""
import argparse
import json
from pathlib import Path
import uuid
from verify_ai_e2e import request

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bridge-url',default='http://127.0.0.1:19080')
    parser.add_argument('--output',type=Path,default=ROOT/'evidence/clean-start.json')
    args=parser.parse_args()
    connection=json.loads((ROOT/'.runtime/connection.json').read_text())
    settings=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    user='clean-smoke-'+uuid.uuid4().hex[:10]
    report={'app_id':connection['app_id'],'base_url':connection['base_url'],'turns':[],'checks':[],'passed':False}

    def state():
        status,result=request(args.bridge_url+'/v2/turns/begin',{'user_id':user,'query':'只读核验','conversation_id':'smoke-inspection'},settings['BUREAU_SERVICE_TOKEN'])
        assert status==200,result
        return result['context']['state']

    def chat(label,query,conversation=''):
        status,result=request(connection['base_url']+'/v1/chat-messages',{'inputs':{},'query':query,'user':user,
            'conversation_id':conversation,'response_mode':'blocking','auto_generate_name':False},connection['api_key'])
        assert status==200,(status,result)
        report['turns'].append({'label':label,'query':query,'answer':result['answer'],
            'message_id':result['message_id'],'conversation_id':result['conversation_id']})
        return result

    def check(label,condition):
        assert condition,label
        report['checks'].append(label)
        print('PASS',label,flush=True)

    try:
        check('new_user_empty',state()['items']=={})
        record=chat('record','相机在蓝色背包，电池还没充，三脚架借给小张了。')
        check('natural_record_real_persistence',len(state()['items'])==3 and '档案已同步' in record['answer'])
        recall=chat('recall','我上次把相机放在哪里？')
        check('recall_new_conversation','蓝色背包' in recall['answer'] and recall['conversation_id']!=record['conversation_id'])
        opened=chat('find','我的相机找不到了')
        ident=state()['last_case']
        resumed=chat('resume','继续办案')
        check('resume_new_conversation',ident in resumed['answer'] and resumed['conversation_id']!=opened['conversation_id'])
        chat('close','相机找到了，在蓝色背包里。',resumed['conversation_id'])
        check('case_closed',state()['cases'][ident]['status']=='closed')
        trip=chat('trip','明天拍照，只核对相机、电池、三脚架，做出门检查。')
        current=state()
        checks=current['trips'][current['last_trip']]['checks']
        check('trip_has_charge_and_borrow_tasks',any(x['item']=='电池' and x['task']=='充电' for x in checks) and any(x['item']=='三脚架' and x['task']=='取回' for x in checks))
        report['passed']=True
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('REPORT',args.output,'passed='+str(report['passed']),flush=True)


if __name__=='__main__':
    main()
