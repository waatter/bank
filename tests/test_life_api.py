"""Actual HTTP v2 workflow; synthetic accounts and no paid model calls."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from backend.model import IntentPlanner, ModelConfig
from backend.server import create_server


class LifeApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server(port=0, db_path=Path(self.directory.name)/'bank.sqlite', planner=IntentPlanner(ModelConfig()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.directory.cleanup()

    def call(self, method, path, body=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        headers = {'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        connection.request(method,path,None if body is None else json.dumps(body),headers)
        response=connection.getresponse(); status=response.status; raw=response.read(); connection.close()
        return status,json.loads(raw)

    def login(self, persona='C', consent=True):
        _, session = self.call('POST','/api/sessions',{'persona':persona})
        token=session['token']; snapshot=session['snapshot']
        if consent:_,snapshot=self.call('PUT','/api/profile',{**snapshot['profile'],'readConsent':True},token)
        return token,snapshot

    @staticmethod
    def scene(**changes):
        result=dict(title='工作设备维修',category='work',amount=1000,earliestDay=2,preferredDay=3,latestDay=10,
                    importance='important',allowSkip=False,allowAlternative=True,alternativeAmount=500,alternativeLabel='已接受的基础维修')
        return {**result,**changes}

    def test_health_version_and_auth_consent(self):
        status,health=self.call('GET','/api/health')
        self.assertEqual((status,health['version']),(200,'3.2.9'))
        self.assertEqual(self.call('GET','/api/life')[0],401)
        token,_=self.login(consent=False)
        self.assertEqual(self.call('GET','/api/life',token=token)[0],403)
        self.assertEqual(self.call('POST','/api/life/analyze',{},token)[0],403)

    def test_financial_health_and_plain_language_terms_are_read_only(self):
        self.assertEqual(self.call('GET','/api/financial-glossary')[0],401)
        token,snapshot=self.login()
        before=self.call('GET','/api/state',token=token)[1]
        status,insights=self.call('GET','/api/insights',token=token)
        self.assertEqual(status,200)
        self.assertEqual(insights['version'],'TROPOS-INSIGHTS-2')
        self.assertIn(insights['healthCheck']['level'],{'urgent','attention','steady'})
        self.assertGreaterEqual(insights['hiddenCosts']['smallOutflowCount'],0)
        self.assertIn('逐项确认',insights['explainability']['decisionBoundary'])
        self.assertNotIn('confirmationToken',json.dumps(insights,ensure_ascii=False))
        status,glossary=self.call('GET','/api/financial-glossary',token=token)
        self.assertEqual(status,200)
        self.assertEqual(glossary['version'],'TROPOS-GLOSSARY-1')
        terms={item['term'] for item in glossary['terms']}
        self.assertTrue({'手续费','自动续费','非保本'}.issubset(terms))
        self.assertEqual(self.call('GET','/api/state',token=token)[1],before)

    def test_model_center_is_private_scoped_and_does_not_mutate_money(self):
        self.assertEqual(self.call('GET','/api/model-center')[0],401)
        first,before=self.login('A'); second,_=self.login('C')
        status,center=self.call('GET','/api/model-center',token=first)
        self.assertEqual(status,200)
        self.assertEqual(center['version'],'TROPOS-MODEL-CENTER-1')
        self.assertEqual((center['selectedProvider'],center['effectiveProvider']),('rules','rules'))
        providers={provider['id']:provider for provider in center['providers']}
        self.assertTrue(providers['rules']['configured'])
        self.assertFalse(providers['openai']['configured'])
        self.assertIn('qwen',providers)
        self.assertIn('mine-qwen',providers)
        self.assertFalse(providers['qwen']['configured'])
        self.assertFalse(providers['mine-qwen']['configured'])
        self.assertFalse(center['canConfigure'])
        self.assertTrue(center['canConfigurePersonal'])
        self.assertFalse(providers['mine-openai']['configured'])
        public=json.dumps(center,ensure_ascii=False).lower()
        self.assertNotIn('api_key',public); self.assertNotIn('secret',public)
        status,reply=self.call('POST','/api/agent',{'message':'查看本月账单'},first)
        self.assertEqual(status,200)
        self.assertEqual(reply['modelRuntime']['effectiveProvider'],'rules')
        self.assertFalse(reply['modelRuntime']['usedForThisMessage'])
        status,error=self.call('PUT','/api/model-center/selection',{'provider':'openai'},first)
        self.assertEqual(status,409); self.assertEqual(error['error']['code'],'MODEL_PROVIDER_NOT_CONFIGURED')
        config={'apiKey':'UNIT_TEST_SECRET','baseUrl':'https://models.invalid/v1','model':'test-model','timeout':3,'confirmation':True}
        status,error=self.call('PUT','/api/model-center/providers/custom',config,first)
        self.assertEqual(status,403); self.assertEqual(error['error']['code'],'MODEL_ADMIN_DISABLED')
        status,personal=self.call('PUT','/api/model-center/personal/providers/custom',config,first)
        self.assertEqual(status,200)
        mine={provider['id']:provider for provider in personal['providers']}['mine-custom']
        self.assertTrue(mine['configured']); self.assertEqual(mine['scope'],'personal')
        self.assertEqual(mine['billing'],'user'); self.assertNotIn('UNIT_TEST_SECRET',json.dumps(personal))
        other=self.call('GET','/api/model-center',token=second)[1]
        self.assertFalse({provider['id']:provider for provider in other['providers']}['mine-custom']['configured'])
        status,personal_selected=self.call('PUT','/api/model-center/selection',{'provider':'mine-custom'},first)
        self.assertEqual(status,200); self.assertEqual(personal_selected['effectiveProvider'],'mine-custom')
        first_uid=self.server.service.authenticate(first); second_uid=self.server.service.authenticate(second)
        self.assertIsNot(self.server.model_center.planner_for(first_uid),self.server.model_center.planner_for(second_uid))
        class PersonalFakePlanner:
            mode='llm'
            config=ModelConfig('UNIT_TEST_SECRET','https://models.invalid/v1','personal-test-model',3)
            def understand(self,_message):
                return {'mode':'llm','intent':{'action':'bills'},
                        'trace':[{'engine':'METIS','detail':'个人模型已返回结构化意图。'}]}
            def decompose(self,_message):
                return {'mode':'llm','steps':None,'reason':None}
        self.server.model_center._personal_provider(first_uid,'custom')['planner']=PersonalFakePlanner()
        status,personal_reply=self.call('POST','/api/agent',{'message':'查看本月账单'},first)
        self.assertEqual(status,200); self.assertEqual(personal_reply['modelRuntime']['scope'],'personal')
        self.assertTrue(personal_reply['modelRuntime']['usedForThisMessage'])
        status,removed=self.call('DELETE','/api/model-center/personal/providers/custom',{'confirmation':True},first)
        self.assertEqual(status,200); self.assertEqual(removed['effectiveProvider'],'rules')
        self.assertFalse({provider['id']:provider for provider in removed['providers']}['mine-custom']['configured'])
        self.server.model_center.allow_runtime_config=True
        status,configured=self.call('PUT','/api/model-center/providers/custom',config,first)
        self.assertEqual(status,200); self.assertTrue(configured['canConfigure'])
        self.assertTrue(next(p for p in configured['providers'] if p['id']=='custom')['configured'])
        self.assertNotIn('UNIT_TEST_SECRET',json.dumps(configured))
        status,selected=self.call('PUT','/api/model-center/selection',{'provider':'custom'},first)
        self.assertEqual(status,200); self.assertEqual(selected['effectiveProvider'],'custom')
        self.assertNotIn('UNIT_TEST_SECRET',json.dumps(selected))
        self.assertEqual(selected['snapshot']['balance'],before['balance'])
        self.assertEqual(selected['snapshot']['user']['revision'],before['user']['revision'])
        other=self.call('GET','/api/model-center',token=second)[1]
        self.assertEqual(other['selectedProvider'],'rules')
        class FakePlanner:
            mode='llm'
            config=ModelConfig('UNIT_TEST_SECRET','https://models.invalid/v1','test-model',3)
            def understand(self,_message):
                return {'mode':'llm','intent':{'action':'bills'},
                        'trace':[{'engine':'METIS','detail':'测试模型已返回结构化意图。'}]}
            def decompose(self,_message):
                return {'mode':'llm','steps':None,'reason':None}
        self.server.model_center._install('custom',FakePlanner(),source='runtime')
        status,model_reply=self.call('POST','/api/agent',{'message':'查看本月账单'},first)
        self.assertEqual(status,200)
        self.assertEqual(model_reply['mode'],'llm')
        self.assertTrue(model_reply['modelRuntime']['usedForThisMessage'])
        self.assertEqual(model_reply['modelRuntime']['effectiveProvider'],'custom')

    def test_life_settings_scenes_and_read_only_projection(self):
        token,snapshot=self.login()
        status,life=self.call('GET','/api/life',token=token)
        self.assertEqual(status,200); self.assertEqual(len(life['catalogue']),8)
        self.assertEqual(self.call('POST','/api/life/settings',{'version':0,'settings':{'expenseShock':0,'delayDays':0}},token)[0],200)
        status,life=self.call('POST','/api/life/scenes',{'version':1,'scene':self.scene(amount=100.49,alternativeAmount=50)},token)
        self.assertEqual(status,201)
        before=self.call('GET','/api/state',token=token)[1]
        status,analysis=self.call('POST','/api/life/analyze',{},token)
        self.assertEqual(status,200); self.assertEqual(analysis['lifeVersion'],2)
        self.assertEqual(len(analysis['policies']),3); self.assertEqual(len(analysis['stressScenarios']),4)
        self.assertEqual(self.call('GET','/api/state',token=token)[1],before)
        self.assertEqual(before['balance'],snapshot['balance'])

    def test_plan_adoption_is_future_obligation_not_payment_and_is_idempotent(self):
        token,snapshot=self.login()
        _,draft=self.call('POST','/api/transfers/propose',{'to':'goal','amount':100,'when':'now','memo':''},token)
        _,life=self.call('POST','/api/life/scenes',{'version':0,'scene':self.scene()},token)
        request={'version':1,'accountRevision':snapshot['user']['revision'],'policyId':'buffer','confirmation':True,'requestKey':'life-http-adopt-1'}
        status,adopted=self.call('POST','/api/life/commit',request,token)
        self.assertEqual(status,200)
        self.assertEqual(adopted['snapshot']['balance'],snapshot['balance'])
        bill=next(b for b in adopted['snapshot']['bills'] if b.get('source')=='life_scene')
        self.assertEqual((bill['amount'],bill['day']),(-500,10))
        self.assertEqual(adopted['snapshot']['user']['revision'],snapshot['user']['revision']+1)
        status,replay=self.call('POST','/api/life/commit',request,token)
        self.assertEqual(status,200); self.assertEqual(replay['receipt']['id'],adopted['receipt']['id'])
        self.assertEqual(replay['snapshot']['bills'],adopted['snapshot']['bills'])
        proposal=draft['proposal']
        status,_=self.call('POST','/api/proposals/'+proposal['id']+'/confirm',{'confirmation':True,'confirmationToken':proposal['confirmationToken']},token)
        self.assertEqual(status,409)
        status,analysis=self.call('POST','/api/life/analyze',{},token)
        self.assertEqual(status,200); self.assertEqual(analysis['policies'][0]['allocations'],[])
        self.assertEqual(analysis['policies'][0]['projections'][0]['pts'][0],snapshot['balance']-analysis['policies'][0]['metrics']['goalAmount'])

    def test_user_scope_and_version_guards(self):
        first,_=self.login('A'); second,_=self.login('C')
        _,life=self.call('POST','/api/life/scenes',{'version':0,'scene':self.scene()},first)
        identity=life['scenes'][0]['id']
        self.assertEqual(self.call('POST','/api/life/scenes/'+identity+'/cancel',{'version':0,'confirmation':True},second)[0],404)
        self.assertEqual(self.call('GET','/api/life',token=second)[1]['scenes'],[])
        self.assertEqual(self.call('POST','/api/life/scenes',{'version':0,'scene':self.scene()},first)[0],409)
        self.assertEqual(self.call('POST','/api/life/commit',{'version':1,'accountRevision':999,'policyId':'buffer','confirmation':True,'requestKey':'stale-http'},first)[0],409)

    def test_client_cannot_supply_allocations_or_skip_confirmation(self):
        token,_=self.login()
        self.assertEqual(self.call('POST','/api/life/analyze',{'allocations':[]},token)[0],400)
        self.assertEqual(self.call('POST','/api/life/scenes',{'version':0,'scene':self.scene(amount='100')},token)[0],400)
        _,life=self.call('POST','/api/life/scenes',{'version':0,'scene':self.scene()},token)
        payload={'version':1,'accountRevision':life['snapshot']['user']['revision'],'policyId':'balanced','confirmation':False,'requestKey':'confirmed-http'}
        self.assertEqual(self.call('POST','/api/life/commit',payload,token)[0],400)
        self.assertEqual(self.call('POST','/api/life/commit',{**payload,'confirmation':True,'allocations':[]},token)[0],400)

    def test_reset_clears_planning_tables_only_for_current_user(self):
        first,_=self.login('A'); second,_=self.login('C')
        for token in (first,second):self.assertEqual(self.call('POST','/api/life/scenes',{'version':0,'scene':self.scene()},token)[0],201)
        status,snapshot=self.call('POST','/api/reset',{'confirmation':True},first)
        self.assertEqual(status,200)
        self.call('PUT','/api/profile',{**snapshot['profile'],'readConsent':True},first)
        clean=self.call('GET','/api/life',token=first)[1]
        self.assertEqual((clean['version'],clean['scenes']),(0,[]))
        self.assertEqual(len(self.call('GET','/api/life',token=second)[1]['scenes']),1)

    def test_life_goal_checks_versions_atomically_and_edits_invalidate_confirmation(self):
        token,snapshot=self.login()
        _,life=self.call('POST','/api/life/settings',{'version':0,'settings':{'delayDays':0,'expenseShock':0,'goalAmount':100}},token)
        request={'version':life['version'],'accountRevision':snapshot['user']['revision'],'amount':100}
        status,draft=self.call('POST','/api/life/goal',request,token)
        self.assertEqual(status,201); proposal=draft['proposal']
        self.assertEqual(proposal['lifeVersion'],1)
        self.assertEqual(draft['snapshot']['balance'],snapshot['balance'])
        _,life=self.call('POST','/api/life/scenes',{'version':1,'scene':self.scene()},token)
        self.assertEqual(life['snapshot']['user']['revision'],snapshot['user']['revision'])
        self.assertEqual(self.call('POST','/api/life/goal',request,token)[0],409)
        newer={**request,'version':2}
        status,blocked=self.call('POST','/api/life/goal',newer,token)
        self.assertEqual(status,409); self.assertEqual(blocked['error']['code'],'LIFE_DRAFTS_PENDING')
        recovered=self.call('GET','/api/proposals',token=token)[1]['proposals'][0]
        self.assertEqual(recovered['status'],'stale'); self.assertNotIn('confirmationToken',recovered)
        status,error=self.call('POST','/api/proposals/'+proposal['id']+'/confirm',{'confirmation':True,'confirmationToken':proposal['confirmationToken']},token)
        self.assertEqual(status,409); self.assertEqual(error['error']['code'],'STALE_LIFE_PLAN')
        self.assertEqual(self.call('GET','/api/state',token=token)[1]['balance'],snapshot['balance'])
