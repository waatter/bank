"""Plain-language financial terms exposed as read-only educational content."""


_TERMS = (
    {
        "id": "available_balance",
        "term": "可用余额",
        "category": "账户",
        "plain": "现在通常可以安排使用的钱，不等于未来扣完账单后还能剩下的钱。",
        "check": "同时查看待入账、待扣款和现金底线。",
    },
    {
        "id": "cash_reserve",
        "term": "现金底线",
        "category": "规划",
        "plain": "你希望始终留在手边、暂时不拿去消费或投资的钱。",
        "check": "它来自你的画像设置，不是银行替你决定的标准答案。",
    },
    {
        "id": "expected_arrival",
        "term": "预计到账",
        "category": "收支",
        "plain": "根据已登记计划推测可能会收到的钱，目前还没有真正入账。",
        "check": "执行依赖这笔钱的计划前，应先确认真实到账。",
    },
    {
        "id": "automatic_renewal",
        "term": "自动续费",
        "category": "消费",
        "plain": "到期时按约定自动扣费继续服务。取消续费通常只是阻止以后扣款，不等于退款。",
        "check": "核对下次扣款日、金额和商户取消回执。",
    },
    {
        "id": "exchange_rate",
        "term": "汇率",
        "category": "跨境",
        "plain": "一种货币能换多少另一种货币。页面报价和最终成交价可能因时间而不同。",
        "check": "同时核对报价时间、买入卖出方向和最终结算币种。",
    },
    {
        "id": "service_fee",
        "term": "手续费",
        "category": "费用",
        "plain": "完成转账、换汇或其他服务时可能另收的钱，不一定包含在页面展示的本金里。",
        "check": "确认收费方、计费方式、币种和是否还有中间行费用。",
    },
    {
        "id": "arrival_time",
        "term": "到账时间",
        "category": "转账",
        "plain": "款项预计到达收款账户的时间，不等于提交时间，也不一定是承诺时间。",
        "check": "节假日、收款行处理和信息复核都可能影响时间。",
    },
    {
        "id": "risk_alert",
        "term": "风险提示",
        "category": "安全",
        "plain": "系统按明确规则提醒你复核，不代表已经认定交易有问题。",
        "check": "查看触发规则和证据，再通过可信渠道自行核对。",
    },
    {
        "id": "non_principal_guaranteed",
        "term": "非保本",
        "category": "理财",
        "plain": "投入的本金存在减少的可能，历史表现不能保证未来结果。",
        "check": "核对最坏损失、持有期限、流动性和产品说明。",
    },
    {
        "id": "installment",
        "term": "分期",
        "category": "信贷",
        "plain": "把一笔付款拆成多期偿还；每期较小不代表总成本更低。",
        "check": "比较总还款额、期数、手续费和提前还款规则。",
    },
)


def glossary():
    """Return a new public payload so callers cannot mutate module constants."""
    return {
        "version": "TROPOS-GLOSSARY-1",
        "terms": [dict(item) for item in _TERMS],
        "boundary": "仅用于把金融表达翻成白话，不构成产品推荐、收益承诺或操作授权。",
    }
