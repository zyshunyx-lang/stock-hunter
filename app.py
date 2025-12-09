import streamlit as st
import pandas as pd
import akshare as ak
import datetime
import pytz
import time
import random
import numpy as np

# ----------------------------------------------------------------------------- 
# 0. 全局配置
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter Data Fetcher (Ultra-Stable)",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 核心辅助函数 & 技术指标计算
# -----------------------------------------------------------------------------
def get_symbol_prefix(code):
    """自动补充代码前缀"""
    if not code or not isinstance(code, str): return code
    if code.startswith('6'): return f"sh{code}"
    if code.startswith('0') or code.startswith('3'): return f"sz{code}"
    if code.startswith('8') or code.startswith('4'): return f"bj{code}"
    return code

def add_technical_indicators(df):
    """为数据增加丰富的技术指标列"""
    try:
        # 1. MACD
        close = df['close']
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['DIF'] = ema12 - ema26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = (df['DIF'] - df['DEA']) * 2

        # 2. 均线系统 (MA)
        for window in [5, 10, 20, 60]:
            df[f'MA{window}'] = close.rolling(window=window).mean()

        # 3. KDJ 指标
        low_list = df['low'].rolling(9, min_periods=9).min()
        high_list = df['high'].rolling(9, min_periods=9).max()
        rsv = (close - low_list) / (high_list - low_list) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']

        # 4. RSI
        def calc_rsi(series, period):
            delta = series.diff()
            up = delta.clip(lower=0)
            down = -1 * delta.clip(upper=0)
            ma_up = up.ewm(com=period-1, adjust=False).mean()
            ma_down = down.ewm(com=period-1, adjust=False).mean()
            rsi = ma_up / (ma_up + ma_down) * 100
            return rsi
        
        df['RSI_6'] = calc_rsi(close, 6)
        df['RSI_12'] = calc_rsi(close, 12)

        # 5. BOLL
        df['BOLL_MID'] = df['close'].rolling(window=20).mean()
        df['BOLL_STD'] = df['close'].rolling(window=20).std()
        df['BOLL_UPPER'] = df['BOLL_MID'] + 2 * df['BOLL_STD']
        df['BOLL_LOWER'] = df['BOLL_MID'] - 2 * df['BOLL_STD']
        
        # 6. VWAP (单日近似)
        if 'amount' in df.columns and 'volume' in df.columns:
             df['VWAP_Day'] = df.apply(lambda x: x['amount'] / x['volume'] if x['volume'] > 0 else x['close'], axis=1)

    except Exception:
        pass
    return df

def clean_data_robust(df):
    """标准化列名"""
    col_map = {
        '日期': 'trade_date', 'date': 'trade_date',
        '开盘': 'open', 'open': 'open', '收盘': 'close', 'close': 'close',
        '最高': 'high', 'high': 'high', '最低': 'low', 'low': 'low',
        '成交量': 'volume', 'volume': 'volume', '成交额': 'amount', 'amount': 'amount',
        '振幅': 'amplitude', '涨跌幅': 'pct_change', '涨跌额': 'change_amount', '换手率': 'turnover_rate'
    }
    df = df.rename(columns=col_map)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    
    num_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'amplitude', 'pct_change', 'turnover_rate']
    for c in num_cols:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

# ----------------------------------------------------------------------------- 
# 2. 稳健的搜索逻辑 (防崩溃设计)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def load_lightweight_market_map():
    """
    仅加载【代码-名称】对应表，不拉取行情数据。
    数据量极小，不易超时。如果失败，返回空字典，不阻断程序运行。
    """
    try:
        df = ak.stock_info_a_code_name() # 这是一个很轻的接口
        df['code'] = df['code'].astype(str).str.strip()
        df['name'] = df['name'].astype(str).str.strip()
        return dict(zip(df['code'], df['name'])), dict(zip(df['name'], df['code'])), True
    except:
        return {}, {}, False

def resolve_stock(query, code2name, name2code, is_map_online):
    """
    解析用户输入：优先查表，查不到则强制联网反查
    """
    query = str(query).strip()
    
    # 1. 尝试从本地字典查
    if is_map_online:
        # 代码匹配
        if query in code2name:
            return query, code2name[query], "本地索引"
        # 名称匹配
        if query in name2code:
            return name2code[query], query, "本地索引"
        # 模糊匹配
        for name, code in name2code.items():
            if query in name:
                return code, name, "模糊匹配"
    
    # 2. 本地没找到 (或索引离线)，且输入像代码 (6位数字)
    #    --> 启动【点对点强制查询】
    if query.isdigit() and len(query) == 6:
        try:
            # 这是一个极轻量的单点查询，几乎不会失败
            df = ak.stock_individual_info_em(symbol=query)
            info = dict(zip(df['item'], df['value']))
            real_name = info.get('股票简称', '未知名称')
            return query, real_name, "强制穿透"
        except:
            return query, "未识别股票", "失败"

    return None, None, "未找到"

# ----------------------------------------------------------------------------- 
# 3. 数据获取引擎
# -----------------------------------------------------------------------------
def get_stock_history_robust(code, days):
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    logs = []
    df = None
    
    # 策略1：东财历史 (最全)
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s_str, end_date=e_str, adjust="qfq")
        if df is not None and not df.empty:
            df = clean_data_robust(df)
            logs.append("✅ 成功源: 东方财富")
    except Exception as e:
        logs.append(f"❌ 东财失败: {str(e)[:50]}")
    
    # 策略2：新浪 (备用)
    if df is None:
        try:
            time.sleep(0.5)
            sym = get_symbol_prefix(code)
            df = ak.stock_zh_a_daily(symbol=sym, start_date=s_str, end_date=e_str, adjust="qfq")
            if df is not None and not df.empty:
                df = clean_data_robust(df)
                logs.append("✅ 成功源: 新浪财经")
        except Exception as e:
            logs.append(f"❌ 新浪失败: {str(e)[:50]}")

    if df is None:
        return None, "所有接口均无响应，可能是IP被暂时限制，请过几分钟再试。", logs

    # 计算指标
    df = add_technical_indicators(df)
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. 用户界面
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter Data Fetcher")
st.sidebar.caption("稳定版 | 防崩溃 | 强制查询")
st.sidebar.markdown("---")

# 1. 尝试加载索引 (静默模式)
code_map, name_map, map_status = load_lightweight_market_map()

# 状态指示灯
if map_status:
    st.sidebar.success(f"🟢 中文名称库已连接 ({len(code_map)}只)")
else:
    st.sidebar.warning("🔴 中文名称库离线 (启用纯代码模式)")

# 2. 输入区
query = st.sidebar.text_input("股票代码/名称", value="002860", help="如本地库离线，请输入6位代码")
days = st.sidebar.slider("回溯天数", 30, 2000, 365)

# 3. 解析目标
target_code, target_name, method = resolve_stock(query, code_map, name_map, map_status)

# 4. 搜索反馈
if target_code:
    if method == "失败":
        st.sidebar.error(f"无法识别代码 {target_code}")
        ready = False
    else:
        st.sidebar.info(f"锁定: **{target_name}** ({target_code})")
        st.sidebar.caption(f"来源: {method}")
        ready = True
else:
    if query:
        st.sidebar.error("❌ 未找到，请尝试输入6位数字代码")
    ready = False

st.sidebar.markdown("---")

if st.sidebar.button("🚀 获取数据", type="primary", disabled=not ready):
    # 即使 method='强制穿透'，我们也拿到了 code，可以获取数据
    with st.spinner(f"正在穿透获取 【{target_name}】 数据..."):
        df, err, logs = get_stock_history_robust(target_code, days)
    
    if err:
        st.error(err)
        with st.expander("调试日志"):
            st.write(logs)
    else:
        # 补全信息
        df['code'] = target_code
        df['name'] = target_name
        
        st.success(f"获取成功! 共 {len(df)} 行数据")
        
        # 概览
        last = df.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("名称", target_name)
        c2.metric("收盘", f"{last['close']:.2f}")
        c3.metric("MACD", f"{last.get('MACD', 0):.3f}")
        c4.metric("RSI(6)", f"{last.get('RSI_6', 0):.2f}")
        
        st.markdown("---")
        
        # 下载
        safe_name = str(target_name).replace("*", "").replace(":", "")
        file_time = datetime.datetime.now().strftime("%Y%m%d")
        file_name = f"【{safe_name}_{file_time}】.csv"
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label=f"📥 下载 CSV 文件 ({file_name})",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary"
        )
        
        st.markdown("### 📋 数据表内容")
        st.dataframe(df.sort_values('trade_date', ascending=False), use_container_width=True, height=600)
