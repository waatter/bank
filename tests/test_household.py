import tempfile
import unittest
from pathlib import Path

from backend.agent_ops import AgentOps
from backend.service import BankError, BankService


class HouseholdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bank = BankService(Path(self.tmp.name) / 'test.sqlite3')
        self.ops = AgentOps(self.bank)

    def tearDown(self):
        self.tmp.cleanup()

    def user(self, persona):
        session = self.bank.session(persona)
        uid = session['snapshot']['user']['id']
        self.bank.save_profile(uid, dict(session['snapshot']['profile'], readConsent=True))
        return uid

    def run_all(self, uid, message):
        mission = self.ops.plan(uid, message)['mission']
        ready = [s['id'] for s in mission['steps'] if s['status'] == 'ready']
        return mission, self.ops.confirm(uid, mission['id'], {'confirmation': True, 'token': mission['token'], 'approve': ready})

    def test_family_approve_pays_within_cap_and_records_usage(self):
        uid = self.user('C')
        mission, out = self.run_all(uid, '批准爸爸买药的代付，拒绝小满的游戏充值')
        self.assertEqual([s['tool'] for s in mission['steps']], ['family_approve', 'family_decline'])
        self.assertEqual([s['status'] for s in out['mission']['steps']], ['done', 'done'])
        self.assertEqual(out['snapshot']['balance'], 20000 - 860)
        family = self.ops.house.overview(uid)['family']
        dad = next(m for m in family['members'] if m['relation'] == '爸爸')
        self.assertEqual(dad['used'], 400 + 860)
        self.assertEqual({r['id']: r['status'] for r in family['requests']}, {'rq_c1': 'declined', 'rq_c2': 'approved'})
        # 已处理的请求不能再批准。
        again = self.ops.plan(uid, '批准爸爸的代付')['mission']['steps'][0]
        self.assertEqual(again['tool'], 'clarify')

    def test_family_category_and_scam_merchant_are_blocked(self):
        uid = self.user('C')
        step = self.ops.plan(uid, '批准小满的游戏充值代付')['mission']['steps'][0]
        self.assertEqual(step['status'], 'blocked')
        self.assertFalse(next(c for c in step['checks'] if c['name'] == '允许类目')['ok'])
        uid = self.user('B')
        step = self.ops.plan(uid, '同意妈妈的代付')['mission']['steps'][0]
        self.assertEqual(step['status'], 'blocked')
        self.assertIn('商户风险', [c['name'] for c in step['checks'] if not c['ok']])

    def test_paused_member_cannot_be_paid(self):
        uid = self.user('C')
        self.run_all(uid, '暂停爸爸的亲情付')
        step = self.ops.plan(uid, '批准爸爸买药的代付')['mission']['steps'][0]
        self.assertEqual(step['status'], 'blocked')
        self.run_all(uid, '恢复爸爸的亲情付')
        self.assertEqual(self.ops.plan(uid, '批准爸爸买药的代付')['mission']['steps'][0]['status'], 'ready')

    def test_ask_parent_only_pays_after_simulated_consent(self):
        uid = self.user('A')
        mission, out = self.run_all(uid, '让妈妈帮我付房租差额500')
        self.assertEqual(out['snapshot']['balance'], 20000)
        ask = self.ops.house.overview(uid)['family']['asks'][0]
        self.assertEqual(ask['status'], 'waiting')
        result = self.ops.house.simulate_ask(uid, ask['id'], {'decision': 'approve'})
        self.assertEqual(result['ask']['status'], 'paid')
        self.assertEqual(result['snapshot']['balance'], 20500)
        again = self.ops.house.simulate_ask(uid, ask['id'], {'decision': 'approve'})
        self.assertEqual(again['snapshot']['balance'], 20500)
        over = self.ops.plan(uid, '让妈妈帮我付600')['mission']['steps'][0]
        self.assertEqual(over['status'], 'blocked')
        with self.assertRaises(BankError):
            self.ops.house.simulate_ask(uid, ask['id'], {'decision': 'maybe'})

    def test_pockets_move_money_through_the_ledger(self):
        uid = self.user('B')
        mission, out = self.run_all(uid, '存500到还款小荷包')
        self.assertEqual(mission['steps'][0]['tool'], 'pocket_deposit')
        self.assertEqual(out['snapshot']['balance'], 19500)
        pocket = self.ops.house.overview(uid)['pockets'][0]
        self.assertEqual(pocket['balance'], 500)
        blocked = self.ops.plan(uid, '从还款小荷包取回800，再看账单')['mission']['steps'][0]
        self.assertEqual(blocked['status'], 'blocked')
        _, out = self.run_all(uid, '从还款小荷包取回200')
        self.assertEqual(out['snapshot']['balance'], 19700)
        _, out = self.run_all(uid, '新建一个旅行小荷包目标5000')
        names = [p['name'] for p in self.ops.house.overview(uid)['pockets']]
        self.assertIn('旅行小荷包', names)

    def test_remaining_savings_cap_spans_goal_and_pocket(self):
        uid = self.user('A')  # 收入不稳：本轮储蓄类建议限额 ¥1,000
        steps = self.ops.plan(uid, '存600到目标储蓄，剩下的存进应急小荷包')['mission']['steps']
        self.assertEqual(steps[1]['amount'], 400)

    def test_budget_and_notes_do_not_touch_balance(self):
        uid = self.user('C')
        _, out = self.run_all(uid, '外卖每月预算不超过1500，记一笔午饭35')
        self.assertEqual(out['snapshot']['balance'], 20000)
        overview = self.ops.house.overview(uid)
        self.assertEqual(overview['budgets']['餐饮'], 1500)
        food = next(r for r in overview['spending']['rows'] if r['category'] == '餐饮')
        self.assertGreaterEqual(food['spent'], 35)
        self.assertNotIn('储蓄', [r['category'] for r in overview['spending']['rows']])

    def test_reset_restores_household(self):
        uid = self.user('C')
        self.run_all(uid, '暂停爸爸的亲情付')
        self.bank.reset(uid)
        self.bank.save_profile(uid, dict(self.bank.snapshot(uid)['profile'], readConsent=True))
        dad = next(m for m in self.ops.house.overview(uid)['family']['members'] if m['relation'] == '爸爸')
        self.assertEqual(dad['status'], 'active')


if __name__ == '__main__':
    unittest.main()
