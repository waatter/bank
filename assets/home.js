/* 首页独立试算。所有金额均为整数元；不读取或修改工作台账本。 */
(function () {
  'use strict';
  const base = Object.freeze({ balance: 12800, salary: 9000, salaryDay: 10, rent: 4200, rentDay: 4, repayment: 1800, repaymentDay: 17, daily: 80, saving: 3000, savingDay: 12, reserve: 4000, days: 30 });
  const scenarios = {
    salary: {
      subtitle: '如果工资晚到 15 天',
      title: '存钱晚一点，日常照常过。',
      original: { salaryDay: 25 },
      adjusted: { salaryDay: 25, savingDay: 27 },
      originalTitle: '还是第 12 天存 ¥3,000，手头会有点紧。',
      originalDetail: '工资还没到账，房租、还款和存钱已经排好了。',
      adjustedTitle: '先把 ¥3,000 的存钱计划往后挪。',
      adjustedDetail: '建议等工资到账再存，房租和还款照常留好。',
      basis: '工资晚到示例：预计工资从第 10 天移到第 25 天。原安排仍在第 12 天存 ¥3,000；新安排建议改到第 27 天。工资到账后，存钱仍需你另行确认。',
      event: { day: 25, label: '预计工资' }
    },
    purchase: {
      subtitle: '如果想换一部新手机',
      title: '同样是喜欢，也能换个预算。',
      original: { expense: 8800, expenseDay: 12 },
      adjusted: { expense: 4800, expenseDay: 12 },
      originalTitle: '按 ¥8,800 的预算买，月底会有点紧。',
      originalDetail: '这笔购物加上原定存钱，余额会低于想留的 ¥4,000。',
      adjustedTitle: '看看 ¥4,800 的选择，手边多留一些。',
      adjustedDetail: '少花 ¥4,000，原来的房租、还款和存钱计划不变。',
      basis: '大件购物示例：工资按原计划在第 10 天到账，第 12 天另买手机。比较 ¥8,800 和 ¥4,800 两种预算；两边都保留第 12 天的 ¥3,000 存钱计划。不包含手续费，也不代表具体商品推荐。',
      event: { day: 12, label: '手机预算' }
    },
    travel: {
      subtitle: '如果这个月想去旅行',
      title: '想去的地方，也看看怎么去。',
      original: { expense: 6800, expenseDay: 15 },
      adjusted: { expense: 4800, expenseDay: 15 },
      originalTitle: '旅行花 ¥6,800，日常预留会少一些。',
      originalDetail: '房租和还款能付，但月底会低于想留的 ¥4,000。',
      adjustedTitle: '试试 ¥4,800 的行程，给日常多留一点。',
      adjustedDetail: '出行日期不变，预算少 ¥2,000；是否采用由你决定。',
      basis: '旅行示例：工资按原计划在第 10 天到账，第 15 天支付旅行费用。比较 ¥6,800 与 ¥4,800 两种行程预算；第 12 天的 ¥3,000 是另一笔日常储蓄，两边都保留，没有重复计作旅行费用。',
      event: { day: 15, label: '旅行预算' }
    }
  };
  const bankTasks = {
    transfer: {
      intent: '给房东转 1,200 元，备注这个月房租。',
      title: '把一笔转账，准备得明明白白。',
      steps: ['听懂收款人、金额和备注', '检查余额、收款人和付款条件', '你核对并确认后，才会记账'],
      state: '等你确认，尚未转账',
      note: '办理后可查原回执，重复确认不会重复扣款。'
    },
    bills: {
      intent: '看看最近的钱都花在哪了。',
      title: '每个汇总，都能找到原来的流水。',
      steps: ['读取你同意查看的账本', '按类别汇总，列出需要留意的流水', '查看原记录，再决定下一步'],
      state: '查询和分析，不会发起付款',
      note: '使用当前可见记录；大额核对线索不等于异常或欺诈结论。'
    },
    subscriptions: {
      intent: '看看哪些会员快续费了。',
      title: '先看清续费，再决定留不留。',
      steps: ['查看当前已登记的订阅和续费日', '比较单项取消后的预计支出', '进入订阅管理，由你逐项确认取消'],
      state: '取消前仍需确认',
      note: '取消影响后续续费，不代表今天退款，也不会自动存钱。'
    }
  };
  let currentScenario = 'salary';
  let currentPlan = 'adjusted';
  const $ = id => document.getElementById(id);
  const money = n => '¥' + n.toLocaleString('zh-CN');

  function project(changes) {
    const input = { ...base, ...changes };
    let balance = input.balance;
    const points = [balance];
    for (let day = 1; day <= input.days; day++) {
      balance -= input.daily;
      if (day === input.salaryDay) balance += input.salary;
      if (day === input.rentDay) balance -= input.rent;
      if (day === input.repaymentDay) balance -= input.repayment;
      if (day === input.savingDay) balance -= input.saving;
      if (day === input.expenseDay) balance -= input.expense || 0;
      points.push(balance);
    }
    const min = Math.min(...points);
    return { points, min, minDay: points.indexOf(min) };
  }

  function chart(original, adjusted, scene) {
    const W = 480, H = 165, left = 12, right = 12, top = 13, bottom = 26;
    const all = original.points.concat(adjusted.points, [base.reserve]);
    const low = Math.max(0, Math.floor(Math.min(...all) / 2000) * 2000 - 1000);
    const high = Math.ceil(Math.max(...all) / 4000) * 4000 + 1000;
    const x = day => left + day / base.days * (W - left - right);
    const y = value => H - bottom - (value - low) / (high - low) * (H - top - bottom);
    const line = data => data.points.map((value, day) => `${day === 0 ? 'M' : 'L'}${x(day).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
    const active = currentPlan === 'original' ? original : adjusted;
    const color = currentPlan === 'original' ? '#c6d2c0' : '#e2edb2';
    const filled = line(active) + ` L${x(30)},${H - bottom} L${left},${H - bottom}Z`;
    const description = `未来 30 天模拟余额。原来安排最低 ${money(original.min)}，出现在第 ${original.minDay} 天；新安排最低 ${money(adjusted.min)}，出现在第 ${adjusted.minDay} 天。想留在手边 ${money(base.reserve)}。每日数字可在页面底部计算说明中查看。`;
    $('cash-chart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="forecast-chart-title forecast-chart-description"><title id="forecast-chart-title">未来一个月，手头的钱怎样变化</title><desc id="forecast-chart-description">${description}</desc><defs><linearGradient id="cash-fill" x1="0" x2="0" y1="0" y2="1"><stop stop-color="${color}" stop-opacity=".16"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>${[8000, 16000].filter(value => value > low && value < high).map(value => `<line x1="${left}" x2="${W - right}" y1="${y(value)}" y2="${y(value)}" stroke="#aac1a1" stroke-opacity=".1"/>`).join('')}<path d="${filled}" fill="url(#cash-fill)"/><line x1="${left}" x2="${W - right}" y1="${y(base.reserve)}" y2="${y(base.reserve)}" stroke="#d4b883" stroke-width="1" stroke-dasharray="3 5"/><text class="reserve-label" x="${left + 2}" y="${y(base.reserve) - 6}">想留的 ¥4,000</text><path class="chart-line" d="${line(original)}" fill="none" stroke="#b0c3ae" stroke-width="${currentPlan === 'original' ? 2.4 : 1.5}" opacity="${currentPlan === 'original' ? 1 : .55}" stroke-dasharray="4 5" stroke-linejoin="round"/><path class="chart-line" d="${line(adjusted)}" fill="none" stroke="#e2edb2" stroke-width="${currentPlan === 'adjusted' ? 2.6 : 1.5}" opacity="${currentPlan === 'adjusted' ? 1 : .4}" stroke-linejoin="round"/><circle cx="${x(active.minDay)}" cy="${y(active.min)}" r="4" fill="${color}" stroke="#213d36" stroke-width="2"/><line x1="${x(scene.event.day)}" x2="${x(scene.event.day)}" y1="${top}" y2="${H - bottom}" stroke="#aac1a1" stroke-opacity=".18" stroke-dasharray="2 4"/><text x="${Math.min(x(scene.event.day) + 5, W - 72)}" y="${top + 2}" style="font-family:var(--font-ui);font-size:8px">${scene.event.label} · 第 ${scene.event.day} 天</text><text x="${left}" y="${H - 5}">今天</text><text x="${x(15)}" y="${H - 5}" text-anchor="middle">第 15 天</text><text x="${W - right}" y="${H - 5}" text-anchor="end">第 30 天</text></svg>`;
  }

  function render() {
    const scene = scenarios[currentScenario];
    const original = project(scene.original), adjusted = project(scene.adjusted);
    const selected = currentPlan === 'original' ? original : adjusted;
    document.querySelectorAll('[data-scenario]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.scenario === currentScenario)));
    document.querySelectorAll('[data-plan]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.plan === currentPlan)));
    $('forecast-sub').textContent = scene.subtitle;
    $('forecast-title').textContent = scene.title;
    $('forecast-min').textContent = money(selected.min);
    const tight = selected.min < base.reserve;
    $('try').dataset.outlook = tight ? 'tight' : 'calm';
    $('forecast-state').textContent = tight ? '低于想留的金额' : '高于想留的金额';
    $('forecast-state').classList.toggle('is-tight', tight);
    $('suggestion-title').textContent = scene[currentPlan + 'Title'];
    $('suggestion-detail').textContent = scene[currentPlan + 'Detail'];
    $('forecast-benefit').textContent = currentPlan === 'adjusted' ? `最紧的时候，多留 ${money(adjusted.min - original.min)}` : `第 ${original.minDay} 天最紧，比想留的少 ${money(Math.max(0, base.reserve - original.min))}`;
    $('scenario-basis').textContent = scene.basis;
    $('daily-balances').innerHTML = original.points.map((value, day) => `<tr><th scope="row">第 ${day} 天</th><td>${money(value)}</td><td>${money(adjusted.points[day])}</td></tr>`).join('');
    chart(original, adjusted, scene);
  }

  function renderBankTask(key) {
    const task = bankTasks[key];
    if (!task) return;
    document.querySelectorAll('[data-bank-task]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.bankTask === key)));
    $('bank-intent').textContent = task.intent;
    $('bank-task-title').textContent = task.title;
    $('bank-task-steps').innerHTML = task.steps.map((text, index) => `<li><span>${String(index + 1).padStart(2, '0')}</span>${text}</li>`).join('');
    $('bank-task-state').textContent = task.state;
    $('bank-task-note').textContent = task.note;
  }
  document.querySelectorAll('[data-bank-task]').forEach(button => button.addEventListener('click', () => renderBankTask(button.dataset.bankTask)));

  document.querySelectorAll('[data-scenario]').forEach(button => button.addEventListener('click', () => {
    currentScenario = button.dataset.scenario;
    currentPlan = 'adjusted';
    render();
  }));
  document.querySelectorAll('[data-plan]').forEach(button => button.addEventListener('click', () => {
    currentPlan = button.dataset.plan;
    render();
  }));
  document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => {
    currentScenario = button.dataset.example;
    currentPlan = 'adjusted';
    render();
    const target = document.querySelector(`[data-scenario="${currentScenario}"]`);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    $('try').scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
    target.focus({ preventScroll: true });
  }));
  $('calculation-link').addEventListener('click', () => { $('calculation-details').open = true; });
  render();
}());
