import streamlit as st
import pandas as pd
import akshare as ak
import datetime
import time
import requests
import re
import io

# ----------------------------------------------------------------------------- 
# 0. 全局配置
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter Pro (AI Ready)",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 极速搜索核心 (新浪/腾讯)
# -----------------------------------------------------------------------------
def search_sina(key):
    """新浪接口搜索"""
    try:
        url = f"http://suggest3.sinajs.cn/suggest/type=&key={key}&name=suggestdata_{int(time.time())}"
        headers = {'Referer': 'http://finance.sina.com.cn/'} 
        r = requests.get(url, headers=headers, timeout=2)
        match = re.search(r'"(.*?)"', r.text)
        if match:
            items = match.group(1).split(';')
            for item in items:
                parts = item.split(',')
                if len(parts) > 4:
                    full_code = parts[3] # 如 sh600519
                    if full_code.startswith(('sh6', 'sz0', 'sz3', 'bj4', 'bj8')):
                        return full_code[2:], parts[4], "新浪接口"
    except: pass
    return None

def search_tencent(key):
    """腾讯接口搜索"""
    try:
        url = f"http://smartbox.gtimg.cn/s3/?v=2&q={key}&t=all"
        r = requests.get(url, timeout=2)
        if 'v_hint="' in r.text:
            raw = r.text.split('v_hint="')[1].split('"')[0]
            parts = raw.split('^')[0].split('~')
            if len(parts) >= 3:
                return parts[2], parts[1], "腾讯接口"
    except: pass
    return None

def get_stock_info_fast(query):
    res = search_sina(query)
    if res: return res
    res = search_tencent(query)
    if res: return res
    return None, None, None

# ----------------------------------------------------------------------------- 
# 2. 数据处理与指标计算
# -----------------------------------------------------------------------------
def add_technical_indicators(df):
    try:
        close = df['close']
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['DIF'] = ema12 - ema26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = (df['DIF'] - df['DEA']) * 2
        # MA
        for w in [5, 10, 20, 60]: df[f'MA{w}'] = close.rolling(window=w).mean()
        # KDJ
        low_min = df['low'].rolling(9).min()
        high_max = df['high'].rolling(9).max()
        rsv = (close - low_min) / (high_max - low_min) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']
        # RSI
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        for p in [6, 12, 24]:
            ma_up = up.ewm(com=p-1, adjust=False).mean()
            ma_down = down.ewm(com=p-1, adjust=False).mean()
            df[f'RSI_{p}'] = ma_up / (ma_up + ma_down) * 100
        # BOLL
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        df['BOLL_UP'] = mid + 2*std
        df['BOLL_LO'] = mid - 2*std
        # VWAP (日内)
        if 'amount' in df.columns:
            df['VWAP'] = df.apply(lambda x: x['amount']/x['volume'] if x['volume']>0 else x['close'], axis=1)
    except: pass
    return df

def clean_data(df):
    col_map = {
        '日期':'trade_date', 'date':'trade_date', '开盘':'open', 'open':'open',
        '收盘':'close', 'close':'close', '最高':'high', 'high':'high', '最低':'low', 'low':'low',
        '成交量':'volume', 'volume':'volume', '成交额':'amount', 'amount':'amount',
        '换手率':'turnover', '涨跌幅':'pct_chg'
    }
    df = df.rename(columns=col_map)
    if 'trade_date' in df:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    return df

# ----------------------------------------------------------------------------- 
# 3. 数据获取引擎
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def fetch_stock_history(code, days):
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    logs = []
    df = None
    
    # 东财 -> 新浪
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s_str, end_date=e_str, adjust="qfq")
        if df is not None and not df.empty:
            df = clean_data(df)
            logs.append("✅ 来源: 东方财富")
    except Exception as e:
        logs.append(f"⚠️ 东财无响应: {e}")
        
    if df is None:
        try:
            prefix = "sh" if code.startswith('6') else ("bj" if code.startswith(('8','4')) else "sz")
            df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", start_date=s_str, end_date=e_str, adjust="qfq")
            if df is not None and not df.empty:
                df = clean_data(df)
                logs.append("✅ 来源: 新浪财经")
        except Exception as e:
            logs.append(f"⚠️ 新浪无响应: {e}")

    if df is None: return None, "无法连接数据源", logs
        
    df = add_technical_indicators(df)
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. Prompt 生成器 (核心)
# -----------------------------------------------------------------------------
def generate_ai_prompt(name, code, days):
    """
    生成高强度的 Gemini 分析提示词
    """
    return f"""
【角色设定】
你是一位拥有20年经验的资深金融分析师，擅长结合量化技术面与基本面进行深度投资研判。

【分析任务】
请基于本CSV文件中提供的【{name} ({code})】过去 {days} 天的全量历史数据，并结合你自主联网搜索的最新信息，撰写一份详尽、客观、理性的投资分析报告。

【执行步骤】
1. 数据深度挖掘（全时段覆盖）：
   - 趋势分析：利用 MA (5/10/20/60) 和 MACD 判断当前处于上涨、下跌还是震荡周期。
   - 量价关系：分析成交量 (Volume) 和 换手率 (Turnover) 的异常波动，识别主力资金的吸筹或出货迹象。
   - 关键位置：根据 BOLL 布林带和筹码密集区，指出当前的强支撑位和压力位。
   - 信号验证：检查 KDJ 和 RSI 是否出现背离、超买或超卖信号。

2. 联网搜索补充（必须执行）：
   - 请搜索该股票最新的【财报摘要】（营收与净利润增长率）。
   - 请搜索近期的【重大新闻】（如重组、订单、政策利好/利空）。
   - 请搜索【行业动态】及【主力资金流向】（如北向资金增持/减持）。

3. 综合研判：
   - 将CSV中的硬数据（技术面）与搜索到的软数据（消息面）进行交叉验证。
   - 比如：股价上涨是否配合了利好消息？缩量下跌是否意味着惜售？

【输出格式】
请输出一份结构清晰的报告，包含：
1. 核心观点（Bullish/Bearish/Neutral）
2. 技术面详评（结合具体指标数值）
3. 基本面与消息面（基于搜索结果）
4. 风险提示（客观列出潜在风险）
5. 结论建议（理性客观，不构成绝对喊单）
"""

# ----------------------------------------------------------------------------- 
# 5. UI 界面
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter Pro (AI Prompt)")
st.sidebar.caption("⚡ 极速搜索 + 🤖 智能提示词")
st.sidebar.markdown("---")

col_in1, col_in2 = st.sidebar.columns([2, 1])
query = col_in1.text_input("代码/名称", value="002860", placeholder="输入代码或名称")
days = col_in2.number_input("天数", 30, 2000, 365)

# 实时搜索
target_code = None
target_name = None

if query:
    with st.spinner("🔍 极速检索中..."):
        s_code, s_name, s_source = get_stock_info_fast(query)
    if s_code:
        st.sidebar.success(f"已锁定: **{s_name}** ({s_code})")
        target_code = s_code
        target_name = s_name
    else:
        st.sidebar.error("❌ 未找到，请尝试手动输入")
        manual_code = st.sidebar.text_input("强制代码", value=query if query.isdigit() else "")
        manual_name = st.sidebar.text_input("强制名称", value="自选股")
        if manual_code and len(manual_code) == 6:
            target_code = manual_code
            target_name = manual_name

st.sidebar.markdown("---")

if st.sidebar.button("🚀 生成 AI 分析数据", type="primary", disabled=not target_code):
    with st.spinner(f"正在拉取 {target_name} 数据并注入 AI 指令..."):
        df, err, logs = fetch_stock_history(target_code, days)
        
    if err:
        st.error(err)
        st.write(logs)
    else:
        # 1. 补全基础信息
        df['code'] = target_code
        df['name'] = target_name
        
        # 2. 【核心】注入 AI Prompt 到新的一列
        # 我们把 Prompt 放在第一列或者最后一列，Gemini 都能读到
        prompt_text = generate_ai_prompt(target_name, target_code, days)
        df['AI_ANALYSIS_PROMPT'] = prompt_text
        
        # 3. 成功展示
        st.success(f"获取成功！AI 提示词已写入 CSV。")
        
        last = df.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("股票", target_name)
        c2.metric("收盘", f"{last['close']:.2f}")
        c3.metric("涨跌", f"{last.get('pct_chg', 0):.2f}%")
        
        # 4. 下载
        safe_name = str(target_name).replace("*", "").replace(":", "")
        file_time = datetime.datetime.now().strftime("%Y%m%d")
        file_name = f"【{safe_name}_{file_time}_AI版】.csv"
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label=f"📥 下载给 Gemini 的数据文件 ({file_name})",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary"
        )
        
        st.info("💡 使用方法：下载此 CSV 发送给 Gemini，它会自动读取 'AI_ANALYSIS_PROMPT' 列中的指令，为你生成深度报告。")
        
        st.markdown("### 📋 数据表预览")
        st.dataframe(df.sort_values('trade_date', ascending=False), use_container_width=True, height=500)
