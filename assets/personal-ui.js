/* A financial situation selects the workbench; an account name never selects a risk class. */
(function () {
  'use strict';
  let adapter;
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => typeof value === 'number' && Number.isFinite(value) ? '¥' + value.toLocaleString('zh-CN', {maximumFractionDigits:2}) : '待核对';
  const routes = new Set(['personal','profile','actions','bills','life','console','transfer','audit']);
  const tasks = {
    cash: [['先看最紧的那一天','逐日查看必要账单、预计收入和缺口。','actions'],['看看哪些续费可以调整','逐项比较取消的影响，保留仍未解决的缺口。','bills'],['重新安排生活计划','比较方案；必要还款不会自动延期。','life']],
    buffer: [['给收入波动留出空间','先看工资晚到时哪些账单仍然要付。','actions'],['逐项核对固定续费','检查已登记会员和下次续费。','bills'],['为一项计划留余地','比较近期安排的现金变化。','life']],
    debt: [['把还款排在一起看','核对每笔还款的金额、日期和原始记录。','bills'],['找到下次还款前的低点','查看现金日历，避免几笔扣款挤在一起。','actions'],['比较生活支出的安排','看看保留缓冲后还能安排多少。','life']],
    goal: [['把近期目标放进日历','在明确用钱日期前比较不同安排。','life'],['确认日常资金够用','目标资金与日常必要支出一起核对。','actions'],['需要存入目标时再确认','单独生成转账草案，核对后决定。','transfer']],
    steady: [['先安排必要支出','确认现金底线，再考虑目标储蓄。','actions'],['逐步完成你的目标','比较不同生活计划的现金影响。','life'],['说说你想办的事','通过银行助手开始查询或准备方案。','console']],
    growth: [['把长期目标说具体','明确金额和期限，比较阶段性资金安排。','life'],['看清现在可安排的资金','以现金路径核对当前余量。','actions'],['另行确认目标储蓄','这里只提供沙箱转账，尚未接入投资申赎。','transfer']],
    review: [['补全你的情况','核对用钱期限、损失承受意愿和相关经验。','profile'],['查看当前账本','查询已登记记录和未来账单。','bills'],['问清楚再决定','让银行助手解释概念和计算依据。','console']]
  };
  function render() {
    const root = document.getElementById('personalMount');
    if (!root || !adapter) return;
    const d = adapter.getData(), r = d.riskProfile;
    if (!r) return;
    document.body.dataset.financialRole = r.role.key;
    if (!r.complete) {
      root.innerHTML = `<section class="panel rp-welcome"><p class="rp-eyebrow">从了解你开始</p><h1>先画出你的情况，再打开适合你的安排。</h1><p>演示账户提供一份账本；你填写的目标、用钱期限和承受意愿，决定页面先关注什么。</p><button class="btn solid" data-rp-edit>完善我的画像 →</button><p class="fine">三种页面风格只影响阅读方式。所有资金操作都需要单独确认。</p></section>`;
      return;
    }
    const role = r.role, f = d.facts, cp = d.cash, profile = d.profile;
    const negative = Number(cp.free) < 0;
    const choices = tasks[role.key] || tasks.review;
    const dims = r.dimensions || [];
    const debt = d.bills.filter(b => ['还款','分期','贷款'].includes(b.kind));
    const detail = role.key === 'debt'
      ? `<h2>先核对这些还款</h2><ul class="rp-ledger">${debt.map(b=>`<li><span>${esc(b.label)}<small>演示第 ${esc(b.day)} 天</small></span><strong>${money(-b.amount)}</strong></li>`).join('') || '<li>当前未登记还款，请核对是否有遗漏。</li>'}</ul>`
      : role.key === 'goal' || role.key === 'growth'
        ? `<h2>${esc(profile.goalName)}</h2><p>目标 ${money(profile.target)}，已在目标账户 ${money(d.goal.saved)}。</p><p>规划时先扣除必要支出与现金底线，再讨论分步安排。</p><button class="btn" data-rp-view="life">比较目标安排 →</button>`
        : `<h2>${negative ? '当前缺口需要继续处理' : '先看账单与收入的时间差'}</h2><p>${negative ? `按当前账本，最低点比现金底线少 ${money(-cp.free)}。取消一项小额续费可能只改善一部分。` : `未来 30 天最低余额 ${money(cp.min)}，现金底线 ${money(profile.reserve)}。`}</p><p>${d.salaryReceived ? '本期模拟工资已到账；未来不会再重复计入。' : '预计工资尚未到账，不能当作今天可支付的余额。'}</p><button class="btn" data-rp-view="actions">展开 30 天现金日历 →</button>`;
    root.innerHTML = `<header class="rp-hero rp-${esc(role.key)}"><div><p class="rp-eyebrow">${esc(d.accountName)}的安排 · ${esc(role.name)}</p><h1>${esc(role.headline)}</h1><p class="rp-lead">${esc(role.description)}</p><div class="rp-hero-actions"><button class="btn solid" data-rp-view="${esc(role.primaryView)}">${esc(role.actionLabel)} →</button><button class="btn" data-rp-edit>核对或修改画像</button></div></div><div class="rp-orbit" aria-hidden="true"><span></span><i></i><b>先了解<br>再安排</b></div></header>
      <dl class="rp-metrics"><div><dt>今天手边余额</dt><dd>${money(f.balance)}</dd></div><div><dt>30 天最低预测余额</dt><dd>${money(cp.min)}</dd></div><div><dt>${negative ? '相对现金底线的缺口' : '现金底线以上余量'}</dt><dd class="${negative ? 'bad-t' : ''}">${money(Math.abs(cp.free))}</dd></div><div><dt>当前波动承受参考</dt><dd class="rp-band">${esc(r.riskBand.label)}</dd></div></dl>
      <section class="rp-section"><div class="block-head"><div><h2>先办这几件事</h2><p>根据你的目标和当前账本调整顺序。</p></div><button class="btn sm" data-rp-view="console">打开银行助手</button></div><div class="rp-tasks">${choices.map(([title,text,view],i)=>`<article class="panel rp-task"><span class="rp-step">0${i+1}</span><h3>${title}</h3><p>${text}</p><button class="btn" data-rp-view="${view}">开始查看 <span aria-hidden="true">↗</span></button></article>`).join('')}</div></section>
      <div class="rp-detail-grid"><section class="panel rp-focus">${detail}</section><section class="panel rp-reasons"><h2>为什么先这样安排</h2><ul>${(r.reasons || []).map(reason=>`<li>${esc(reason)}</li>`).join('')}</ul><div class="rp-checks"><strong>每一步由你决定</strong><p>如有未登记的支出，请先补充核对。转账、取消续费等操作，仍需单独确认。</p></div><button class="btn sm" data-rp-view="profile">查看完整画像与变化记录</button></section></div>
      <section class="rp-section"><div class="block-head"><div><h2>你的画像有多个维度</h2><p>情况变化会更新财务判断；长期偏好由你更正。</p></div></div><div class="rp-dimensions">${dims.map(dim=>`<article><p>${esc(dim.label)}</p><strong>${esc(dim.value)}</strong><small>${esc(dim.detail)}</small></article>`).join('')}</div></section>
      <p class="rp-footnote">${esc(r.version)} · 研究启发的原型规则，尚未做真实用户量表验证；波动档位不表示收益、风险概率或产品适当性结论。页面安排不授予转账权限。</p>`;
  }
  function init(value) {
    if(adapter) return;
    adapter = value;
    document.getElementById('personalMount')?.addEventListener('click', event => {
      if(event.target.closest('[data-rp-edit]')) { adapter.editProfile(); return; }
      const button = event.target.closest('[data-rp-view]');
      if(button && routes.has(button.dataset.rpView)) adapter.openView(button.dataset.rpView);
    });
    render();
  }
  window.TroposPersonalUI = {init,render};
})();
