"""Generate the Dify 0.15.3 AI Chatflow without credentials."""
import json
from pathlib import Path
import yaml
root=Path(__file__).resolve().parents[1]
nodes,edges=[],[]
def node(ident,kind,title,x,y,**data):
    nodes.append({'id':ident,'type':'custom','width':260,'height':100,'position':{'x':x,'y':y},'positionAbsolute':{'x':x,'y':y},'sourcePosition':'right','targetPosition':'left','data':{'type':kind,'title':title,'desc':'','selected':False,**data}})
def edge(source,target,handle='source'):
    kinds={n['id']:n['data']['type'] for n in nodes}
    edges.append({'id':source+'-'+handle+'-'+target,'source':source,'target':target,'sourceHandle':handle,'targetHandle':'target','type':'custom','data':{'sourceType':kinds[source],'targetType':kinds[target],'isInIteration':False}})
def code(ident,title,filename,variables,outputs,x,y):
    node(ident,'code',title,x,y,code_language='python3',code=(root/'workflow'/filename).read_text(encoding='utf-8'),variables=[{'variable':k,'value_selector':v} for k,v in variables.items()],outputs={k:{'type':v,'children':None} for k,v in outputs.items()})
def http(ident,title,path,payload,x,y):
    node(ident,'http-request',title,x,y,method='post',url='{{#env.MEMORY_BRIDGE_URL#}}'+path,authorization={'type':'api-key','config':{'type':'bearer','api_key':'{{#env.BUREAU_TOKEN#}}','header':'Authorization'}},headers='Content-Type:application/json',params='',body={'type':'json','data':[{'key':'','type':'text','value':'{{#'+payload+'#}}'}]},timeout={'connect':5,'read':40,'write':10},error_strategy='default-value',default_value=[{'key':'body','type':'string','value':'{"ok":false}'},{'key':'status_code','type':'number','value':503}],retry_config={'retry_enabled':False,'max_retries':0,'retry_interval':0},variables=[])
def gate(ident,source,x,y):
    node(ident,'if-else','检查本轮是否可继续',x,y,cases=[{'id':'yes','case_id':'yes','logical_operator':'and','conditions':[{'id':ident+'-ok','variable_selector':[source,'ok'],'varType':'string','comparison_operator':'is','value':'yes'}]}])
def answer(ident,source,x,y):
    node(ident,'answer','回复',x,y,answer='{{#'+source+'.answer#}}',variables=[])
def llm(ident,title,prompt,context,x,y,temp):
    node(ident,'llm',title,x,y,model={'provider':'openai_api_compatible','name':'deepseek-flash','mode':'chat','completion_params':{'temperature':temp,'max_tokens':1400}},prompt_template=[{'role':'system','text':(root/'workflow'/prompt).read_text(encoding='utf-8')},{'role':'user','text':'本轮用户原话：{{#sys.query#}}\n服务端档案上下文：{{#'+context+'#}}'}],context={'enabled':False,'variable_selector':[]},vision={'enabled':False},error_strategy='default-value',default_value=[{'key':'text','type':'string','value':'{}'}],retry_config={'retry_enabled':False,'max_retries':0,'retry_interval':0})
node('start','start','开始',0,100,variables=[])
code('prepare','安全编码输入与用户标识','ai_prepare.py',{'query':['sys','query'],'user_id':['sys','user_id'],'conversation_id':['sys','conversation_id']},{'payload':'string'},300,100)
http('begin','恢复个人档案与待答问题','/v2/turns/begin','prepare.payload',600,100)
code('begin_check','核验档案读取','ai_begin_check.py',{'body':['begin','body'],'status_code':['begin','status_code']},{'ok':'string','turn_id':'string','context':'string','answer':'string'},900,100)
gate('begin_gate','begin_check',1200,100)
answer('begin_failed','begin_check',1500,500)
llm('understand','DeepSeek · 理解与多意图提取','intent_prompt.txt','begin_check.context',1500,100,0.1)
code('intent_check','校验可执行动作','ai_intent_check.py',{'text':['understand','text'],'turn_id':['begin_check','turn_id']},{'ok':'string','payload':'string','answer':'string'},1800,100)
gate('intent_gate','intent_check',2100,100)
answer('intent_failed','intent_check',2400,500)
http('resolve','真实检索与当前状态','/v2/context/resolve','intent_check.payload',2400,100)
code('resolve_check','核验检索依据','ai_resolve_check.py',{'body':['resolve','body'],'status_code':['resolve','status_code']},{'ok':'string','context':'string','answer':'string'},2700,100)
gate('resolve_gate','resolve_check',3000,100)
answer('resolve_failed','resolve_check',3300,500)
llm('plan','DeepSeek · 排查追问与出门计划','planner_prompt.txt','resolve_check.context',3300,100,0.4)
code('plan_check','约束计划与证据字段','ai_plan_check.py',{'text':['plan','text'],'turn_id':['begin_check','turn_id']},{'payload':'string'},3600,100)
http('commit','业务校验与PowerContext事件提交','/v2/turns/commit','plan_check.payload',3900,100)
code('commit_check','校验真实回执后展示','ai_commit_check.py',{'body':['commit','body'],'status_code':['commit','status_code']},{'answer':'string'},4200,100)
answer('result','commit_check',4500,100)
chain=['start','prepare','begin','begin_check','begin_gate','understand','intent_check','intent_gate','resolve','resolve_check','resolve_gate','plan','plan_check','commit','commit_check','result']
for a,b in zip(chain,chain[1:]): edge(a,b,'yes' if a.endswith('_gate') else 'source')
for a,b in [('begin_gate','begin_failed'),('intent_gate','intent_failed'),('resolve_gate','resolve_failed')]: edge(a,b,'false')
features=json.loads((root/'workflow/features.json').read_text(encoding='utf-8'))
features['opening_statement']='我是忘事管理局值班员阿档。东西找不到，我们一起排查；准备出门，我帮你核对。\n\n告诉我物品放哪里，换新对话也能接着办案。'
features['suggested_questions']=['相机在蓝色背包，电池还没充，三脚架借给小张了。','我的相机找不到了','继续办案','明天出去拍照，做出门检查']
doc={'app':{'name':'忘事管理局 · 失物调查与出门检查','description':'DeepSeek理解与计划 + Dify核心编排 + PowerContext个人记忆。会追问、能续案、可结案的生活事务调查局。','mode':'advanced-chat','icon':'🕵️','icon_background':'#FFEAD5','use_icon_as_answer_icon':False},'kind':'app','version':'0.1.3','workflow':{'conversation_variables':[],'environment_variables':[{'id':'90786281-9ede-44ae-95ca-f8d09b234703','name':'MEMORY_BRIDGE_URL','value':'http://memory-bridge:19080','value_type':'string'},{'id':'b4a94949-8ad8-4d51-81f1-ce403ace84aa','name':'BUREAU_TOKEN','value':'__SET_BY_BOOTSTRAP__','value_type':'secret'}],'features':features,'graph':{'nodes':nodes,'edges':edges,'viewport':{'x':0,'y':0,'zoom':0.5}}}}
(root/'LostFound.yml').write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False,width=110),encoding='utf-8',newline='\n')
print('Generated AI Chatflow:',len(nodes),'nodes,',len(edges),'edges')
