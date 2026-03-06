import json
import datetime as dt
from urllib import request
import pandas as pd
from jqdata import *
import numpy as np
import talib 

# 飞书通知函数
def notify_feishu(title, content, feishu_webhook, feishu_enable=True):
    """发送飞书机器人消息；失败则写日志，不影响交易主流程。"""
    
    def format_cn_time(trigger_dt=None):
        """格式化当前时间为中文格式"""
        if trigger_dt is None:
            trigger_dt = dt.datetime.now()
        return trigger_dt.strftime('%Y年%m月%d日%H:%M:%S')

    # 格式化当前时间
    time_text = format_cn_time()
    message = f'[{time_text}] {title}\n{content}'
    print(message)  # 输出日志

    if not feishu_enable or not feishu_webhook:
        return

    # 构建飞书消息内容
    payload = {
        'msg_type': 'text',
        'content': {
            'text': message,
        },
    }
    data = json.dumps(payload).encode('utf-8')

    # 发送请求到飞书 webhook
    try:
        req = request.Request(
            feishu_webhook,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        request.urlopen(req, timeout=3)
    except Exception as e:
        print(f'飞书通知发送失败: {e}')


# 封装交易消息格式
def build_trade_message(action_text, stock, price, target_position_tenths, reason, trigger_dt=None):
    """构建交易信息消息"""
    name = get_security_info(stock).display_name
    time_text = format_cn_time(trigger_dt)
    return (
        f'【首创证券】尊敬的客户您好，您订阅的组合【{g.strategy_name}】{action_text}{name}（{stock}），'
        f'成交价格{price:.3f}元，建议仓位{target_position_tenths:.1f}成。'
        f'触发原因：{reason}。投资建议仅供参考，风险自担。时间：{time_text}。'
    )


# 示例：发送初始化通知
feishu_webhook = 'https://your-feishu-webhook-url'  # 填写你的 Feishu Webhook URL
notify_feishu('策略启动', '初始化完成：日频调仓+日频风控+周度优化；目标持仓4-5只；进攻最小夏普阈值=2.5。', feishu_webhook)


# =========================
# 示例策略集成（实际交易逻辑）：
# =========================

def get_agile_trend(security, context):
    """
    敏捷趋势判断：获取现价、MA20、以及MA20的斜率
    """
    if get_security_info(security).start_date >= context.previous_date: 
        return 0, 99999, -1

    # 获取过去25天的价格数据
    price = get_price(security, end_date=context.previous_date, count=25, fields=['close'])
    if len(price) < 22:
        return 0, 99999, -1

    closes = price['close'].values
    ma20_curr = closes[-20:].mean()  # 当前20日均线
    ma20_prev = closes[-21:-1].mean()  # 前20日均线
    current = closes[-1]  # 当前价格
    
    # 斜率：当前MA20 - 昨日MA20 / 昨日MA20
    slope = (ma20_curr - ma20_prev) / ma20_prev
    
    return current, ma20_curr, slope

def rebalance_portfolio(context):
    """
    日频调仓逻辑：
    1. 每个交易日开盘后
    2. A股趋势进攻 / 全球防御
    3. 买卖点实时推送飞书
    """
    price, ma20, ma20_slope = get_agile_trend('000300.XSHG', context)
    is_ashare_active = (price > ma20) and (ma20_slope > -0.001)

    target_list = []  # 根据策略生成的目标股票列表

    if is_ashare_active:
        # 进攻信号
        log.info(f"【日内进攻】A股站上均线(价>{ma20:.0f}) -> 启动九星竞速")
        target_list = get_best_stars_sharpe(context, g.target_hold_num)

    else:
        # 防御信号
        log.info('【日内防御】A股破位 -> 启动防御组合')
        target_list = get_defensive_basket(context, g.min_hold_num)

    target_list = normalize_target_list(target_list)

    # 卖出
    current_holdings = [
        s for s in context.portfolio.positions if context.portfolio.positions[s].total_amount > 0
    ]
    for stock in current_holdings:
        if stock not in target_list:
            place_order_target(context, stock, 0, reason='日频调仓卖出')

    # 买入
    if target_list:
        available_value = context.portfolio.total_value * 0.99
        per_value = available_value / len(target_list)
        for target in target_list:
            place_order_target_value(context, target, per_value, reason='日频调仓买入')

        # 通知飞书交易行为
        content = build_trade_message(
            '调仓', target_list, price, per_value, '日频调仓', trigger_dt=context.current_dt
        )
        notify_feishu('交易信号', content, feishu_webhook)


def weekly_review_and_optimize(context):
    """
    根据最近周度收益，自动微调参数并推送飞书周报。
    说明：聚宽环境不允许自改代码文件，这里做的是参数级“自我迭代”。
    """
    # 获取并计算周度收益
    current_nav = g.nav_history[-1][1]
    prev_week_nav = g.nav_history[-6][1]
    weekly_ret = (current_nav / prev_week_nav) - 1

    # 设置优化动作
    action = '参数维持不变'
    if weekly_ret > 0.03:
        # 如果收益大于某个阈值，适度提高进攻性
        g.initial_stop_loss = min(-0.06, g.initial_stop_loss - 0.002)
        g.rsi_hot_threshold = min(86, g.rsi_hot_threshold + 1)
        action = '本周收益较好，适度提高进攻性'

    # 汇总结果
    content = (
        f"日期: {context.current_dt.date()}\n"
        f"周收益: {weekly_ret:.2%}\n"
        f"优化动作: {action}\n"
        f"当前参数: stop_loss={g.initial_stop_loss:.2%}, "
        f"rsi_hot={g.rsi_hot_threshold}, "
        f"sharpe_bonus_threshold={g.sharpe_bonus_threshold:.2%}"
    )

    # 发送复盘通知
    notify_feishu('周度策略复盘与自适应优化', content, feishu_webhook)


def daily_check(context):
    """每日风控检查，触发止损时推送飞书报警"""
    yesterday = context.previous_date
    current_holdings = [
        s for s in context.portfolio.positions if context.portfolio.positions[s].total_amount > 0
    ]
    for stock in current_holdings:
        pos = context.portfolio.positions[stock]
        cost = pos.avg_cost

        price_data = get_price(stock, end_date=yesterday, count=1, fields=['close'])
        if len(price_data) == 0:
            continue

        current_price = price_data['close'][-1]
        returns = (current_price - cost) / cost

        if returns < g.initial_stop_loss:
            # 止损触发，发送风控报警到飞书
            content = f"【止损警报】股票：{stock} 当前价格：{current_price}，止损触发，返回：{returns:.2%}"
            notify_feishu('风控警报', content, feishu_webhook)
            place_order_target(context, stock, 0, reason=f'硬止损触发 {returns:.2%}')
            continue
