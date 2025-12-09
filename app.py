import streamlit as st
import pandas as pd
import akshare as ak
import datetime
import time
import random
import requests
import re

# ----------------------------------------------------------------------------- 
# 0. 全局配置
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter Data Fetcher (Fast)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 多源搜索核心 (新浪/腾讯/东财) - 替代本地大表
# -----------------------------------------------------------------------------
def search_sina(key):
    """
    新浪搜索接口：同时支持代码和中文名称
    返回: (code, name, market_prefix) 或 None
    """
    try:
        # 新浪建议接口
        url = f"http://suggest3.sinajs.cn/suggest/type=&key={key}&name=suggestdata_{int(time.time())}"
        headers = {'Referer': 'http://finance.sina.com.cn/'} 
        r = requests.get(url, headers=headers, timeout=2)
        content = r.text
        
        # 解析返回: var suggestdata_...="隆基绿能,11,601012,sh601012,...";
        match = re.search(r'"(.*?)"', content)
        if match:
            data_str = match.group(1)
            if not data_str: return None
            
            # 结果可能有多个，用分号隔开，我们取第一个A股结果
            items = data_str.split(';')
            for item in items:
                parts = item.split(',')
                if len(parts) > 4:
                    # parts[3] 是带前缀的代码 (如 sh601012)
                    # parts[4] 是中文名
                    full_code = parts[3]
                    name = parts[4]
                    
                    # 简单过滤: 只看 A 股 (sh6/sz0/sz3/bj4/bj8)
                    if full_code.startswith(('sh6', 'sz0', 'sz3', 'bj4', 'bj8')):
                        clean_code = full_code[2:] # 去掉 sh/sz/bj
                        return clean_code, name, full_code[:2]
    except:
        pass
    return None

def search_tencent(key):
    """
    腾讯搜索接口 (作为新浪的备用)
    """
    try:
        # 腾讯智能搜索接口
        url = f"http://smartbox.gtimg.cn/s3/?v=2&q={key}&t=all"
        r = requests.get(url, timeout=2)
        content = r.text 
        # 返回格式: v_hint="sz002860~星帅尔~002860~XS~A股~...^..."
        
        if 'v_hint="' in content:
            raw = content.split('v_hint="')[1].split('"')[0]
            if not raw or raw == "N": return None
            
            # 取第一条结果
            first_result = raw.split('^')[0]
            parts = first_result.split('~')
            if len(parts) >= 3:
                # parts[0] = sz002860 (full code)
                # parts[1] = 星帅尔 (name)
                # parts[2] = 002860 (code)
                full_code = parts[0]
                name = parts[1]
                code = parts[2]
                return code, name, full_code[:2]
    except:
        pass
    return None

def get_stock_info_fast(query):
    """
    统一搜索入口：先查新浪，再查腾讯
    """
    # 1. 尝试新浪
    res = search_sina(query)
    if res: return res[0], res[1], "新浪接口"
    
    # 2. 尝试腾讯
    res = search_tencent(query)
    if res: return res[0], res[1], "腾讯接口"
    
    return None, None, None

# ----------------------------------------------------------------------------- 
# 2. 数据处理与指标
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
# 3. 数据获取引擎 (多源)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def fetch_stock_history(code, days):
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    logs = []
    df = None
    
    # 1. 东财 (最全)
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s_str, end_date=e_str, adjust="qfq")
        if df is not None and not df.empty:
            df = clean_data(df)
            logs.append("✅ 行情来源: 东方财富")
    except Exception as e:
        logs.append(f"⚠️ 东财接口无响应: {e}")
        
    # 2. 新浪 (备用)
    if df is None:
        try:
            # 需要前缀
            if code.startswith('6'): prefix = "sh"
            elif code.startswith('8') or code.startswith('4'): prefix = "bj"
            else: prefix = "sz"
            
            df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", start_date=s_str, end_date=e_str, adjust="qfq")
            if df is not None and not df.empty:
                df = clean_data(df)
                logs.append("✅ 行情来源: 新浪财经")
        except Exception as e:
            logs.append(f"⚠️ 新浪接口无响应: {e}")

    if df is None:
        return None, "所有数据源均无法连接，请稍后重试。", logs
        
    df = add_technical_indicators(df)
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. 用户界面
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter Pro (极速版)")
st.sidebar.caption("🔎 基于新浪/腾讯实时接口")
st.sidebar.markdown("---")

# --- 输入区 ---
col_in1, col_in2 = st.sidebar.columns([2, 1])
query = col_in1.text_input("股票代码或名称", value="002860", placeholder="输入代码/中文名")
days = col_in2.number_input("天数", 30, 2000, 365)

# --- 实时搜索逻辑 ---
# 每次输入变化，直接调用轻量级接口查询，不需要本地大表
target_code = None
target_name = None

if query:
    with st.spinner("🔍 正在全网搜索..."):
        s_code, s_name, s_source = get_stock_info_fast(query)
    
    if s_code:
        st.sidebar.success(f"已锁定: **{s_name}** ({s_code})")
        st.sidebar.caption(f"识别来源: {s_source}")
        target_code = s_code
        target_name = s_name
    else:
        st.sidebar.error("❌ 未找到股票，请检查输入")
        # 允许强制手动模式
        st.sidebar.markdown("---")
        st.sidebar.warning("如果确定代码正确，可在下方强制执行")
        manual_code = st.sidebar.text_input("强制代码", value=query if query.isdigit() else "")
        manual_name = st.sidebar.text_input("强制名称", value="自选股")
        if manual_code and len(manual_code) == 6:
            target_code = manual_code
            target_name = manual_name

st.sidebar.markdown("---")

# --- 执行 ---
if st.sidebar.button("🚀 获取数据", type="primary", disabled=not target_code):
    with st.spinner(f"正在拉取 【{target_name}】 数据..."):
        df, err, logs = fetch_stock_history(target_code, days)
        
    if err:
        st.error(err)
        st.write(logs)
    else:
        # 成功
        st.success(f"获取成功: {target_name} ({target_code})")
        
        # 补全
        df['code'] = target_code
        df['name'] = target_name
        
        # 展示
        last = df.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("股票", target_name)
        c2.metric("收盘", f"{last['close']:.2f}")
        c3.metric("涨跌", f"{last.get('pct_chg', 0):.2f}%")
        c4.metric("换手", f"{last.get('turnover', 0):.2f}%")
        
        st.markdown("---")
        
        # 下载
        safe_name = str(target_name).replace("*", "").replace(":", "")
        file_time = datetime.datetime.now().strftime("%Y%m%d")
        file_name = f"【{safe_name}_{file_time}】.csv"
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label=f"📥 下载 CSV: {file_name}",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary"
        )
        
        st.caption("已包含 MACD, KDJ, MA 等指标，适合 Gemini 分析。")
        
        # 预览
        st.markdown("### 📋 数据表")
        st.dataframe(df.sort_values('trade_date', ascending=False), use_container_width=True, height=500)
