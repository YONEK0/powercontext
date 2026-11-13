"""Paid, live DeepSeek -> published Dify -> real PowerContext acceptance tests.

Run in the WSL deployment root. Reads private connection/admin files. Reports
contain synthetic test content and real receipts, never provider credentials.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from bootstrap_app import api

ROOT=Path(__file__).resolve().parents[1]
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url, data=None, token=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None,headers=headers)
    try:
        with OPENER.open(req,timeout=110) as response: return response.status,json.load(response)
    except urllib.error.HTTPError as error:
        return error.code,json.loads(error.read())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--with-outages',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'evidence/ai-e2e.json')
    args=parser.parse_args()
    connection=json.loads((ROOT/'.runtime/connection.json').read_text())
    run=uuid.uuid4().hex[:10]
    user='ai-acceptance-'+run
    project='lostfound-dify'
    # Respect a custom deployment project without reading or logging compose secrets.
    import os
    project=os.environ.get('LOSTFOUND_PROJECT',project)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'run_id':run,'app_id':connection['app_id'],
        'method':'Published Dify advanced-chat; real deepseek-flash; real PowerContext; assertions inspect Dify persisted node outputs',
        'checks':[],'turns':[],'passed':False}
    model_enabled=True

    def check(label,condition=True,**details):
        assert condition,(label,details)
        report['checks'].append({'label':label,**details})
        print('PASS',label,json.dumps(details,ensure_ascii=False),flush=True)

    def compose(*command):
        r=subprocess.run(['bash',str(ROOT/'scripts/compose.sh'),*command],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr[-1500:])

    def trace(message):
        ident=str(uuid.UUID(message))
        sql="""select json_build_object('workflow_run_id',m.workflow_run_id,'user_id',m.from_end_user_id,
          'nodes',(select json_agg(json_build_object('node_id',n.node_id,'status',n.status,'outputs',n.outputs::jsonb))
          from workflow_node_executions n where n.workflow_run_id=m.workflow_run_id)) from messages m where m.id='%s';""" % ident
        r=subprocess.run(['docker','exec',project+'-db-1','psql','-U','postgres','-d','dify','-Atc',sql],capture_output=True,text=True,check=True)
        return json.loads(r.stdout)

    def chat(label,query,conversation='',other_user=None,expect_commit=True):
        started=time.monotonic()
        status,data=request(connection['base_url']+'/v1/chat-messages',{
            'inputs':{},'query':query,'response_mode':'blocking','user':other_user or user,
            'conversation_id':conversation,'auto_generate_name':False},connection['api_key'])
        assert status==200,(label,status,data)
        t=trace(data['message_id'])
        nodes={n['node_id']:n for n in t['nodes']}
        commit=json.loads(nodes['commit']['outputs']['body']) if 'commit' in nodes and nodes['commit'].get('outputs',{}).get('body') else None
        row={'label':label,'query':query,'answer':data.get('answer'),'conversation_id':data['conversation_id'],
             'message_id':data['message_id'],'workflow_run_id':t['workflow_run_id'],'user_id':t['user_id'],
             'latency_ms':round((time.monotonic()-started)*1000),'node_statuses':{k:v['status'] for k,v in nodes.items()},
             'intent_text':nodes.get('understand',{}).get('outputs',{}).get('text'),
             'planner_text':nodes.get('plan',{}).get('outputs',{}).get('text'),'commit':commit,'usage':data.get('metadata',{}).get('usage')}
        report['turns'].append(row)
        if expect_commit:
            assert commit and commit.get('ok') is True,(label,data.get('answer'),row['intent_text'])
            assert data['answer']==commit['answer'],(label,'answer mismatch')
            if commit['mutated']: assert commit.get('memory_id','').startswith('art_'),(label,'missing real receipt')
        print('TURN',label,'focus='+str(commit.get('focus') if commit else None),'ms='+str(row['latency_ms']),flush=True)
        return data,commit

    def target(result,name):
        return next(x for x in result['state']['items'].values() if x['name']==name)

    def trip(result):
        return result['state']['trips'][result['state']['last_trip']]

    def task(result,name,task_name):
        return next(x for x in trip(result)['checks'] if x['item']==name and x['task']==task_name)

    def ready():
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                status,data=request('http://127.0.0.1:19080/health')
                if status==200 and data.get('ok'): return
            except OSError: pass
            time.sleep(1)
        raise RuntimeError('Memory services did not recover')

    admin=json.loads((ROOT/'.runtime/admin.json').read_text())
    login=api(connection['base_url'],'/console/api/login',{**admin,'language':'zh-Hans','remember_me':True})
    admin_token=login['data']['access_token']
    model_path='/console/api/workspaces/current/model-providers/openai_api_compatible/models/'
    model_body={'model':'deepseek-flash','model_type':'llm'}
    try:
        ready()
        data,state=chat('natural_multi_record','相机在蓝色背包，电池还没充，三脚架借给小张了。')
        conversation=data['conversation_id']
        check('three_facts_persisted',len(state['state']['items'])==3 and target(state,'相机')['location']['text']=='蓝色背包' and target(state,'电池')['condition']=='needs_charge' and target(state,'三脚架')['borrower']=='小张',memory_id=state['memory_id'])
        _,recalled=chat('recall_new_conversation','我上次把相机放在哪里？')
        check('natural_recall_across_sessions',target(recalled,'相机')['location']['text']=='蓝色背包')
        opened,case_state=chat('open_case','我的相机找不到了',conversation)
        case_id=case_state['state']['last_case']
        check('case_opened',case_state['state']['cases'][case_id]['status']=='open',case_id=case_id)
        _,checked=chat('checked_no_match','蓝色背包找过了，没有相机。',conversation)
        check('checked_location_persisted','蓝色背包' in checked['state']['cases'][case_id]['checked'])
        _,clued=chat('new_clue','昨天换成黑色相机包去拍照了，可能忘在里面。',conversation)
        check('clue_saved',bool(clued['state']['cases'][case_id]['clues']))
        resumed,resume_state=chat('resume_new_conversation','继续办案')
        check('resumed_same_case_new_session',resumed['conversation_id']!=conversation and resume_state['state']['last_case']==case_id and '蓝色背包' in resume_state['state']['cases'][case_id]['checked'],old_conversation=conversation,new_conversation=resumed['conversation_id'])
        check('no_repeat_checked_suggestion','蓝色背包' not in (resume_state['state'].get('pending') or {}).get('locations',[]))
        found,found_state=chat('found_and_close','相机找到了，在黑色相机包里。',resumed['conversation_id'])
        check('found_closes_case_not_usual',found_state['state']['cases'][case_id]['status']=='closed' and target(found_state,'相机')['usual_location'] is None)
        _,stored=chat('set_current_and_usual','现在把相机放在书桌右侧抽屉，以后也固定放这里。',found['conversation_id'])
        camera=target(stored,'相机')
        check('explicit_current_and_usual',camera['location']['text']=='书桌右侧抽屉' and camera['usual_location']['text']=='书桌右侧抽屉')
        trip_data,trip_state=chat('create_trip','明天出去拍照，只核对相机、电池、三脚架，做出门检查。')
        trip_conv=trip_data['conversation_id']
        check('trip_uses_real_borrow_and_charge_facts',task(trip_state,'电池','充电')['status']=='pending' and task(trip_state,'三脚架','取回')['status']=='pending')
        _,charged=chat('charge_is_not_packed','电池充好了，但还没装包。',trip_conv)
        check('charge_and_pack_separate',task(charged,'电池','充电')['status']=='done' and task(charged,'电池','装包')['status']=='pending')
        _,negative=chat('negative_not_marked_done','相机还没装包。',trip_conv)
        check('negative_does_not_complete_task',task(negative,'相机','装包')['status']=='pending')
        _,packed=chat('pack_two_items','相机已装包，电池也装包了。',trip_conv)
        check('two_explicit_pack_updates',task(packed,'相机','装包')['status']=='done' and task(packed,'电池','装包')['status']=='done')
        _,unpacked=chat('retract_packed_state','相机从包里拿出来了，还没重新装包。',trip_conv)
        check('negative_feedback_reopens_task',task(unpacked,'相机','装包')['status']=='pending')
        _,repacked=chat('pack_again_after_retraction','相机已经重新装包了。',trip_conv)
        check('explicit_pack_again',task(repacked,'相机','装包')['status']=='done')
        _,excluded=chat('exclude_borrowed_item','这次不带三脚架。',trip_conv)
        check('excluded_only_this_trip',all(x['status']=='excluded' for x in trip(excluded)['checks'] if x['item']=='三脚架') and target(excluded,'三脚架')['borrower']=='小张')
        cross_trip,cross_state=chat('trip_new_conversation','查看我这次的出门清单，还差什么？')
        check('trip_progress_across_sessions',cross_trip['conversation_id']!=trip_conv and task(cross_state,'相机','装包')['status']=='done' and task(cross_state,'电池','装包')['status']=='done')
        _,new_trip=chat('new_trip_resets_progress','后天出去拍照，重新做一份出门检查，只核对相机、电池、三脚架。')
        check('new_trip_no_completed_state_leak',trip(new_trip)['id']!=trip(cross_state)['id'] and all(x['status']=='pending' for x in trip(new_trip)['checks']))
        _,first_case=chat('second_investigation_camera','相机又找不到了')
        _,two_cases=chat('second_investigation_keys','钥匙也找不到了')
        _,ambiguous=chat('multi_case_clarification','继续办案')
        check('ambiguous_cases_do_not_mix',len([c for c in two_cases['state']['cases'].values() if c['status']=='open'])==2 and ambiguous['focus'][0]=='clarify' and not ambiguous['mutated'])
        _,isolated=chat('different_user_isolated','我上次把相机放在哪里？',other_user=user+'-other')
        check('different_user_empty_archive',isolated['state']['items']=={} and isolated['scope_id']!=two_cases['scope_id'])
        if args.with_outages:
            api(connection['base_url'],model_path+'disable',model_body,admin_token,method='PATCH')
            model_enabled=False
            failed,fail_state=chat('model_unavailable_no_fake_success','故障测试物品放在红色抽屉。',expect_commit=False)
            check('model_failure_stops_mutation',fail_state is None and '尚未修改' in failed['answer'])
            api(connection['base_url'],model_path+'enable',model_body,admin_token,method='PATCH')
            model_enabled=True
            compose('stop','powercontext')
            failed,fail_state=chat('memory_unavailable_no_fake_success','故障测试物品放在红色抽屉。',expect_commit=False)
            check('memory_failure_stops_before_llm',fail_state is None and '无法读取' in failed['answer'])
            compose('start','powercontext'); ready()
            compose('up','-d','--force-recreate','powercontext','memory-bridge'); ready()
            _,restored=chat('recreated_containers_restore_memory','查看我这次的出门清单，还差什么？')
            check('container_recreation_keeps_scope_and_state',restored['focus'][0]=='trip' and restored['scope_id']==ambiguous['scope_id'] and restored['state']['trips']==ambiguous['state']['trips'] and restored['state']['cases']==ambiguous['state']['cases'])
            _,check_failure=chat('failed_turns_not_archived','故障测试物品在哪里？')
            check('failed_turns_left_no_item',all(x['name']!='故障测试物品' for x in check_failure['state']['items'].values()))
        report['passed']=True
    except Exception as error:
        report['error']=str(error)
        print('FAIL',report['error'],flush=True)
        raise
    finally:
        if not model_enabled:
            api(connection['base_url'],model_path+'enable',model_body,admin_token,method='PATCH')
        if args.with_outages: compose('up','-d','powercontext','memory-bridge')
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('REPORT',args.output,'passed='+str(report['passed']),flush=True)


if __name__=='__main__':
    main()
