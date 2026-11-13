"""Domain unit tests with an in-memory fake; live integration is verify_ai_e2e.py."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI, HTTPException
import bureau as b


class BureauTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        b.ROOT = Path(self.temp.name)
        b.TOKEN = 'unit-service-token'
        b.TURNS.clear()
        self.rows, self.scopes = {}, {}
        self.fail_after_write = False
        app = FastAPI()
        b.install_bureau(app, self.pc, lambda s: copy.deepcopy(self.rows[s]), lambda r:r['artifact']['artifact_id'])
        self.routes = {r.path:r.endpoint for r in app.routes if r.path.startswith('/v2/')}
        self.auth = 'Bearer ' + b.TOKEN

    def tearDown(self):
        self.temp.cleanup()

    def pc(self, method, path, data):
        if path == '/v1/scopes':
            key = data['idempotency_key']
            if key not in self.scopes:
                self.scopes[key] = 'scp_' + str(len(self.scopes)+1)
                self.rows[self.scopes[key]] = []
            return {'scope_id':self.scopes[key]}
        if path == '/v1/memory/search':
            return {'hits':[]}
        if path == '/v1/memory/remember':
            rows = self.rows[data['scope_id']]
            row = {**data, 'artifact':{'artifact_id':'art_' + str(len(rows)+1)}}
            rows.append(row)
            if self.fail_after_write:
                self.fail_after_write = False
                raise HTTPException(503, 'test timeout after actual write')
            return {'records':[row]}
        raise AssertionError(path)

    def begin(self, query, user='alice', conversation='one'):
        return self.routes['/v2/turns/begin'](b.Begin(user_id=user,conversation_id=conversation,query=query),self.auth)

    def resolve(self, ident, actions):
        return self.routes['/v2/context/resolve'](b.Resolve(turn_id=ident,actions=actions),self.auth)

    def commit(self, ident, plan=None):
        return self.routes['/v2/turns/commit'](b.Commit(turn_id=ident,plan=plan or {}),self.auth)

    def turn(self, query, actions, plan=None, user='alice', conversation='one'):
        ident = self.begin(query,user,conversation)['turn_id']
        self.resolve(ident,actions)
        return self.commit(ident,plan)

    def seed(self):
        return self.turn('相机在蓝色背包，电池还没充，三脚架借给小张了',[
            {'type':'record','item':'相机','location':'蓝色背包'},
            {'type':'record','item':'电池','condition':'needs_charge'},
            {'type':'record','item':'三脚架','borrower':'小张'}])

    def test_user_isolation_and_replay(self):
        first = self.seed()
        self.assertEqual(len(first['state']['items']),3)
        reopened = self.begin('我的相机在哪里',conversation='new')['context']['state']
        self.assertEqual(first['state'],reopened)
        self.assertEqual(self.begin('相机',user='bob')['context']['state']['items'],{})

    def test_checked_places_and_new_conversation(self):
        self.seed()
        opened = self.turn('相机找不到了',[{'type':'find','item':'相机'}])
        case = next(iter(opened['state']['cases'].values()))
        checked = self.turn('没有',[{'type':'checked','locations':['蓝色背包']}],{'next_location':'蓝色背包','suggestion':'继续查蓝色背包','question':'查蓝色背包了吗'})
        self.assertEqual(checked['state']['cases'][case['id']]['checked'],['蓝色背包'])
        self.assertNotIn('继续查蓝色背包',checked['answer'])
        resumed = self.turn('继续办案',[{'type':'continue'}],conversation='two')
        self.assertEqual(resumed['state']['cases'][case['id']]['checked'],['蓝色背包'])

    def test_found_is_not_usual_location(self):
        self.seed(); self.turn('相机找不到了',[{'type':'find','item':'相机'}])
        result=self.turn('找到了，在黑色相机包',[{'type':'found','item':'相机','location':'黑色相机包'}])
        target=next(x for x in result['state']['items'].values() if x['name']=='相机')
        self.assertIsNone(target['usual_location'])
        self.assertEqual(target['location']['text'],'黑色相机包')
        self.assertEqual(next(iter(result['state']['cases'].values()))['status'],'closed')

    def test_negative_found_rolls_back(self):
        self.seed(); before=self.turn('相机找不到了',[{'type':'find','item':'相机'}])
        result=self.turn('还没找到',[{'type':'found','item':'相机'}])
        self.assertFalse(result['mutated']); self.assertEqual(result['state'],before['state'])

    def test_trip_charge_pack_borrow_and_exclusion(self):
        self.seed()
        trip=self.turn('明天拍照，做出门检查',[{'type':'trip','activity':'拍照'}],{'checklist':['相机','电池','三脚架']})
        charged=self.turn('电池充好了，还没装包',[{'type':'trip_update','item':'电池','task':'充电','status':'done'}])
        checks=charged['state']['trips'][charged['state']['last_trip']]['checks']
        self.assertEqual(next(x['status'] for x in checks if x['item']=='电池' and x['task']=='充电'),'done')
        self.assertEqual(next(x['status'] for x in checks if x['item']=='电池' and x['task']=='装包'),'pending')
        denied=self.turn('三脚架已装包',[{'type':'trip_update','item':'三脚架','task':'装包','status':'done'}])
        self.assertFalse(denied['mutated']); self.assertIn('借出',denied['answer'])
        excluded=self.turn('这次不带三脚架',[{'type':'trip_update','item':'三脚架','status':'excluded'}])
        checks=excluded['state']['trips'][excluded['state']['last_trip']]['checks']
        self.assertTrue(all(x['status']=='excluded' for x in checks if x['item']=='三脚架'))
        fresh=self.turn('后天拍照，重新做出门检查',[{'type':'trip','activity':'拍照'}],{'checklist':['相机','电池','三脚架']})
        new_checks=fresh['state']['trips'][fresh['state']['last_trip']]['checks']
        self.assertTrue(all(x['status']=='pending' for x in new_checks))
        self.assertNotEqual(trip['state']['last_trip'],fresh['state']['last_trip'])

    def test_fabricated_location_and_partial_batch_roll_back(self):
        result=self.turn('相机在书桌',[{'type':'record','item':'相机','location':'书桌'},
            {'type':'record','item':'钥匙','location':'保险箱'}])
        self.assertFalse(result['mutated']); self.assertEqual(result['state']['items'],{})

    def test_two_open_cases_need_name_in_new_conversation(self):
        self.turn('相机找不到了',[{'type':'find','item':'相机'}])
        before=self.turn('钥匙也找不到了',[{'type':'find','item':'钥匙'}])
        result=self.turn('继续办案',[{'type':'continue'}],conversation='different')
        self.assertFalse(result['mutated']); self.assertIn('多个',result['answer'])
        self.assertEqual(before['state'],result['state'])

    def test_retry_same_turn_is_idempotent(self):
        ident=self.begin('相机在书桌')['turn_id']
        self.resolve(ident,[{'type':'record','item':'相机','location':'书桌'}])
        first=self.commit(ident); second=self.commit(ident)
        self.assertTrue(second['duplicate']); self.assertEqual(first['memory_id'],second['memory_id'])
        self.assertEqual(len(self.rows[first['scope_id']]),1)

    def test_timeout_after_write_reconciles_receipt(self):
        self.fail_after_write=True
        result=self.seed()
        self.assertTrue(result['mutated']); self.assertEqual(len(self.rows[result['scope_id']]),1)

    def test_concurrent_old_version_cannot_overwrite(self):
        old=self.begin('相机在书桌')['turn_id']
        self.resolve(old,[{'type':'record','item':'相机','location':'书桌'}])
        self.turn('钥匙在玄关',[{'type':'record','item':'钥匙','location':'玄关'}])
        with self.assertRaises(HTTPException) as caught: self.commit(old)
        self.assertEqual(caught.exception.status_code,409)

    def test_authentication_and_invalid_actions(self):
        with self.assertRaises(HTTPException) as caught:
            self.routes['/v2/turns/begin'](b.Begin(user_id='alice',query='x'),'Bearer wrong')
        self.assertEqual(caught.exception.status_code,401)
        ident=self.begin('x')['turn_id']
        with self.assertRaises(HTTPException): self.resolve(ident,[{'type':'delete_everything'}])

    def test_model_invented_completion_rejected(self):
        self.seed(); self.turn('明天拍照',[{'type':'trip','activity':'拍照'}])
        result=self.turn('电池还没装包',[{'type':'trip_update','item':'电池','task':'装包','status':'done'}])
        self.assertFalse(result['mutated']); self.assertIn('还没有明确完成',result['answer'])

    def test_requested_trip_items_override_planner(self):
        self.seed()
        result=self.turn('明天拍照，只核对相机和电池',[{'type':'trip','activity':'拍照','items':['相机','电池']}],{'checklist':['三脚架','无人机']})
        checks=result['state']['trips'][result['state']['last_trip']]['checks']
        self.assertEqual({x['item'] for x in checks},{'相机','电池'})

    def test_unpacked_feedback_reopens_completed_task(self):
        self.seed(); self.turn('明天拍照',[{'type':'trip','activity':'拍照'}])
        self.turn('相机已装包',[{'type':'trip_update','item':'相机','task':'装包','status':'done'}])
        result=self.turn('相机从包里拿出来了，还没装包',[{'type':'trip_update','item':'相机','task':'装包','status':'pending'}])
        checks=result['state']['trips'][result['state']['last_trip']]['checks']
        check=next(x for x in checks if x['item']=='相机' and x['task']=='装包')
        self.assertEqual(check['status'],'pending'); self.assertNotIn('confirmed_at',check)

    def test_same_activity_selects_latest_or_explicit_date(self):
        self.seed()
        first=self.turn('明天拍照',[{'type':'trip','activity':'拍照'}])
        latest=self.turn('后天拍照',[{'type':'trip','activity':'拍照'}])
        shown=self.turn('这次的拍照出门清单',[{'type':'trip_status','activity':'拍照'}])
        self.assertEqual(shown['focus'][1],latest['state']['last_trip'])
        tomorrow=self.turn('明天的拍照清单',[{'type':'trip_status','activity':'拍照'}])
        self.assertEqual(tomorrow['focus'][1],first['state']['last_trip'])


if __name__=='__main__':
    unittest.main(verbosity=2)
