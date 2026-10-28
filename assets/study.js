/* Manual, memory-only study log. No application adapter, storage or network. */
(function (root) {
  'use strict';
  const SCHEMA = 'TROPOS-USER-TRIAL-1';
  const TASKS = [
    {id:'profile_correction', title:'01 · 纠正我的情况', prompt:'在演示账户建立或检查“我的情况”。情景：原来准备长期存的钱，改为三个月后要用；把用钱期限与现金底线改为主持人提供的合成条件，再找到更新后的安排。', question:'页面风格、你的风险意愿、当前现金承受能力分别由什么决定？改风格会自动改变风险意愿吗？'},
    {id:'expected_vs_received', title:'02 · 预计收入与实际到账', prompt:'在晓舟演示账户找到预计工资，先模拟工资延期，观察今天余额与未来安排；再按主持人的合成情景登记到账，说明什么信息发生了变化。', question:'预计工资可以当作今天已到账的钱使用吗？延期和实际到账分别改变了什么？'},
    {id:'transfer_correction', title:'03 · 同名转账与金额更正', prompt:'在现金允许的知远演示账户说“给王磊转100元”。辨明同名收款人，选择同事；再说“改成200元”。核对旧草案与新草案，确认前停下来说明对象和金额，再按本人意愿单独确认演示转账。', question:'系统怎样知道是哪位王磊？旧的100元方案还能执行吗？说“改成200元”是否已经付款？'},
    {id:'cancellation_impact', title:'04 · 查看取消的影响', prompt:'在晓舟工资延期情景里查看会员续费，选一项比较取消影响。找出少支出金额、现金最低点和剩余缺口；本任务只比较，不确认取消。', question:'取消续费会立刻退款或增加今天余额吗？少支出后是否一定消除了现金缺口？'},
    {id:'cancellation_confirmation', title:'05 · 单项确认取消', prompt:'选择一个合成订阅，准备取消方案。阅读单项确认卡，说明对象、金额、影响与仍存在的缺口；在本人愿意后单独确认一次，并找到原回执。', question:'准备方案、输入“确认”和点击单项确认分别做了什么？这是否会取消所有订阅？'},
    {id:'timeout_recovery', title:'06 · 超时后恢复', prompt:'由主持人准备一个尚未取消的合成订阅，启用工作台的取消超时演示。亲自确认后，在结果暂不明确时查询原方案；刷新或恢复办理记录，核对原回执，不新建第二笔请求。', question:'超时是否就表示失败？怎样核对原回执并避免重复提交？'}
  ];
  const clone = value => JSON.parse(JSON.stringify(value));
  const oneOf = (value, choices) => choices.includes(value);
  function sessionId(crypto) {
    // Actual phones may reach a LAN HTTP origin without randomUUID support.
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const bytes=crypto.getRandomValues(new Uint8Array(16));
    bytes[6]=(bytes[6]&15)|64; bytes[8]=(bytes[8]&63)|128;
    const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function count(value) {
    if (value === null) return null;
    if (!Number.isSafeInteger(value) || value < 0) throw new Error('次数须为非负整数或留空。');
    return value;
  }
  function text(value) {
    if (value === null || value === '') return null;
    if (typeof value !== 'string' || value.length > 1000) throw new Error('文字最多 1000 字。');
    // Minimize accidental direct identifiers; this is not a complete anonymizer.
    if (/(?:https?:\/\/|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\d{7,})/i.test(value)) throw new Error('请删除链接、邮箱或连续长数字，勿输入真实身份与银行资料。');
    return value.trim() || null;
  }
  function createTrial(settings, clock = () => ({wall:new Date().toISOString(), monotonic:performance.now()})) {
    if (!/^P\d{3,6}$/.test(settings.participant_id)) throw new Error('请使用 P + 3–6 位数字的匿名编号。');
    if (!oneOf(settings.record_kind, ['real_participant','internal_acceptance','example']) ||
        !oneOf(settings.interaction_source, ['human_manual','automated_test']) ||
        (settings.record_kind === 'real_participant' && settings.interaction_source !== 'human_manual')) throw new Error('数据标签或操作来源不正确。');
    const e = settings.environment;
    if (!e || !oneOf(e.device,['desktop','real-phone','emulation']) || !oneOf(e.network,['Wi-Fi','cellular','unknown']) || !oneOf(e.vpn,['on','off','unknown'])) throw new Error('请主动选择设备、网络与 VPN 状态。');
    if (settings.consent !== true) throw new Error('须由参与者主动勾选同意。');
    const initial = clock();
    const record = {schema:SCHEMA, session_id:settings.session_id, participant_id:settings.participant_id,
      record_kind:settings.record_kind, interaction_source:settings.interaction_source,
      consent:{accepted:true, method:settings.interaction_source === 'human_manual' ? 'participant_manual_checkbox' : 'internal_test_only', at:initial.wall},
      environment:{device:e.device,network:e.network,vpn:e.vpn,self_reported:true}, created_at:initial.wall,
      tasks:TASKS.map(({id}) => ({id,status:'not_started',started_at:null,ended_at:null,duration:null,completed:null,help_count:null,misunderstanding_count:null,misunderstanding_notes:null,comprehension_answer:null,comprehension_correct:null,feedback:null}))};
    const starts = new Map();
    let exports = 0;
    const task = id => { const t = record.tasks.find(t => t.id === id); if (!t) throw new Error('未知任务。'); return t; };
    return {
      snapshot:() => clone(record),
      start(id) {
        const t = task(id);
        if (t.status !== 'not_started' || record.tasks.some(t => t.status === 'in_progress')) throw new Error('只能开始未开始的任务；请先结束当前任务。');
        const now = clock(); t.status='in_progress'; t.started_at=now.wall; starts.set(id,now.monotonic);
      },
      end(id, fields) {
        const t = task(id);
        if (t.status !== 'in_progress') throw new Error('任务必须先开始，结束后不可改写。');
        if (typeof fields.completed !== 'boolean') throw new Error('请主动选择是否完成。');
        const answer = text(fields.comprehension_answer);
        if (fields.comprehension_correct !== null && typeof fields.comprehension_correct !== 'boolean') throw new Error('理解核对须为对、错或未采集。');
        if (fields.comprehension_correct !== null && !answer) throw new Error('判断理解前，请记录匿名回答摘要。');
        const values = {completed:fields.completed, help_count:count(fields.help_count), misunderstanding_count:count(fields.misunderstanding_count),
          misunderstanding_notes:text(fields.misunderstanding_notes), comprehension_answer:answer,
          comprehension_correct:fields.comprehension_correct, feedback:text(fields.feedback)};
        const now = clock(), duration = (now.monotonic - starts.get(id)) / 1000;
        if (!Number.isFinite(duration) || duration < 0 || Date.parse(now.wall) < Date.parse(t.started_at)) throw new Error('设备计时异常，请保留本场并重新记录。');
        Object.assign(t,values,{status:'ended',ended_at:now.wall,duration}); Object.freeze(t);
      },
      exportRecord() { return {...clone(record), exported_at:clock().wall, export_index:++exports}; }
    };
  }
  // Node tests exercise internal acceptance records only; browser exposes no control API.
  if (typeof module !== 'undefined' && module.exports) module.exports = {SCHEMA,TASKS,createTrial,sessionId};
  if (!root.document) return;
  const doc = root.document, $ = id => doc.getElementById(id);
  let trial = null, manualConsent = false, exportRequests = 0;
  const archive = [];
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function notice(message) { $('notice').textContent = message; }
  function nullableNumber(el) { return el.value.trim() === '' ? null : Number(el.value); }
  function nullableBool(el) { return el.value === '' ? null : el.value === 'true'; }
  function showTasks() {
    const state = trial.snapshot(), busy = state.tasks.some(t => t.status === 'in_progress');
    $('tasks').innerHTML = TASKS.map((spec,i) => {
      const t = state.tasks[i], ended=t.status === 'ended', active=t.status === 'in_progress';
      return `<section class="card" data-task="${spec.id}" aria-labelledby="title-${spec.id}"><div class="task-header"><h2 id="title-${spec.id}">${spec.title}</h2><span class="badge">${ended?'已结束 · 已锁定':active?'计时中':'未开始'}</span></div><p class="task-prompt">${spec.prompt}</p>${ended ? `<div class="task-result"><h3>原始结果（只读）</h3><dl>${[['是否完成',t.completed?'是':'否'],['原始耗时（秒）',t.duration],['帮助次数',t.help_count ?? '未采集'],['理解错误次数',t.misunderstanding_count ?? '未采集'],['误解摘要',t.misunderstanding_notes],['核对回答',t.comprehension_answer],['理解核对',t.comprehension_correct === null ? '未采集' : t.comprehension_correct ? '正确' : '有误'],['主观反馈',t.feedback],['开始',t.started_at],['结束',t.ended_at]].map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>` : active ? `<form data-end="${spec.id}" autocomplete="off"><p class="check-question">结束前理解核对：${spec.question}</p><label>是否完成（主动选择）<select name="completed" required><option value="">请选择</option><option value="true">完成</option><option value="false">未完成 / 主动停止</option></select></label><div class="fields"><label>帮助次数<input name="help_count" type="number" min="0" step="1" placeholder="未采集；无帮助请填0"></label><label>理解错误次数<input name="misunderstanding_count" type="number" min="0" step="1" placeholder="未采集；确认无误解填0"></label></div><button type="button" class="secondary" data-help="${spec.id}">人工记录 +1 次帮助</button><label>误解摘要<textarea name="misunderstanding_notes" maxlength="1000" placeholder="只记任务误解，勿填个人资料"></textarea></label><label>参与者理解核对回答摘要<textarea name="comprehension_answer" maxlength="1000" placeholder="未采集可留空；勿抄真实聊天"></textarea></label><label>主持人依据原回答核对<select name="comprehension_correct"><option value="">未采集 / 无法判定</option><option value="true">正确</option><option value="false">有误</option></select></label><label>参与者主观反馈<textarea name="feedback" maxlength="1000" placeholder="自愿填写；请勿含任何真实身份或银行资料"></textarea></label><small>留空保留为未采集；结束后锁定原结果。中途导出不会保存尚未提交的这些输入，请先结束任务。</small><div class="actions"><button type="submit">结束并锁定本任务</button></div></form>` : `<button type="button" data-start="${spec.id}" ${busy?'disabled':''}>开始任务</button>`}</section>`;
    }).join('');
  }
  $('consent').addEventListener('change', event => {
    manualConsent = event.isTrusted === true && $('consent').checked === true;
    if (!manualConsent) $('consent').checked = false;
  });
  $('setup-form').addEventListener('submit', event => {
    event.preventDefault();
    if (event.isTrusted !== true) { notice('记录只接受人工操作。'); return; }
    try {
      if (!manualConsent || !$('consent').checked) throw new Error('请参与者本人主动勾选同意。');
      trial=createTrial({session_id:sessionId(root.crypto),participant_id:$('participant-id').value,
        record_kind:$('record-kind').value,interaction_source:'human_manual',consent:true,
        environment:{device:$('device').value,network:$('network').value,vpn:$('vpn').value}});
      $('setup').hidden=true; $('session').hidden=false; exportRequests=0;
      const s=trial.snapshot(), labels={real_participant:'真人试用 · 自报，未经身份验证',internal_acceptance:'内部验收数据 · 不是真人结果',example:'示例数据 · 不是真人结果'};
      $('session-label').textContent=`${s.participant_id} · ${labels[s.record_kind]}`;
      $('session-meta').textContent=`${s.environment.device} / ${s.environment.network} / VPN ${s.environment.vpn} · ${s.session_id}${s.environment.device==='emulation'?' · 设备模拟，不计真人结果':''}`;
      $('export-note').textContent='尚未导出。'; notice('本场已建立；尚无任务结果。'); showTasks();
    } catch (error) { notice(error.message); }
  });
  $('tasks').addEventListener('click', event => {
    if (event.isTrusted !== true) return;
    const start = event.target.closest('[data-start]'), help = event.target.closest('[data-help]');
    try {
      if (start) { trial.start(start.dataset.start); showTasks(); notice('开始计时。'); }
      if (help) { const el=help.closest('form').elements.namedItem('help_count'); const current=nullableNumber(el); el.value=count(current === null ? 1 : current+1); }
    } catch (error) { notice(error.message); }
  });
  $('tasks').addEventListener('submit', event => {
    event.preventDefault();
    if (event.isTrusted !== true) return;
    const form=event.target.closest('form[data-end]'); if (!form) return;
    try {
      const el=name=>form.elements.namedItem(name);
      trial.end(form.dataset.end,{completed:nullableBool(el('completed')),help_count:nullableNumber(el('help_count')),
        misunderstanding_count:nullableNumber(el('misunderstanding_count')),misunderstanding_notes:el('misunderstanding_notes').value,
        comprehension_answer:el('comprehension_answer').value,comprehension_correct:nullableBool(el('comprehension_correct')),feedback:el('feedback').value});
      showTasks(); notice('原结果已锁定，不能改写；需要更正请保留原导出并另开一场。');
    } catch (error) { notice(error.message); }
  });
  $('export').addEventListener('click', event => {
    if (event.isTrusted !== true || !trial) return;
    const r=trial.exportRecord(), filename=`tropos-trial-${r.participant_id}-${r.session_id}-${r.export_index}.json`;
    const url=root.URL.createObjectURL(new root.Blob([JSON.stringify(r,null,2)+'\n'],{type:'application/json'}));
    const link=doc.createElement('a'); link.href=url; link.download=filename; doc.body.appendChild(link); link.click(); link.remove();
    root.setTimeout(()=>root.URL.revokeObjectURL(url),1000); exportRequests++;
    $('export-note').textContent=`已发起 ${exportRequests} 次下载，最近文件：${filename}。请确认设备保存成功；已导出文件不会随本页重开而更改。`;
  });
  $('restart').addEventListener('click', event => {
    if (event.isTrusted !== true || !trial) return;
    const message=exportRequests ? '请确认已保存导出文件。重开会清空当前页记录，已导出文件仍保留在设备上。' : '本场尚未导出，重开将丢失内存记录。建议先取消并下载。仍要重开吗？';
    if (!root.confirm(message)) return;
    const r=trial.snapshot(); archive.push(`${r.participant_id} / ${r.session_id}：${exportRequests?'曾发起下载，请保留设备上的原文件':'未导出，原内存记录已清空'}`);
    trial=null; manualConsent=false; $('setup-form').reset(); $('consent').checked=false; $('setup').hidden=false; $('session').hidden=true; $('tasks').innerHTML='';
    $('archive-note').textContent='本页重开提示（仅本页内存）：\n'+archive.join('\n'); notice('新一场需重新主动选择，并由参与者本人勾选同意。');
  });
  root.addEventListener('beforeunload',event=>{if(trial){event.preventDefault();event.returnValue='';}});
})(typeof window === 'undefined' ? globalThis : window);
