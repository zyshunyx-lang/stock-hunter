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
    page_title="Hunter Data Fetcher (Pro)",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 核心辅助函数 & 技术指标计算
# -----------------------------------------------------------------------------
def get_symbol_prefix(code):
    """自动补充代码前缀 (用于备用接口)"""
    if not code or not isinstance(code, str): return code
    if code.startswith('6'): return f"sh{code}"
    if code.startswith('0') or code.startswith('3'): return f"sz{code}"
    if code.startswith('8') or code.startswith('4'): return f"bj{code}"
    return code

def add_technical_indicators(df):
    """
    为数据增加丰富的技术指标列
    """
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

        # 4. RSI (相对强弱指标 6, 12, 24)
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

        # 5. Bollinger Bands (布林带)
        df['BOLL_MID'] = df['close'].rolling(window=20).mean()
        df['BOLL_STD'] = df['close'].rolling(window=20).std()
        df['BOLL_UPPER'] = df['BOLL_MID'] + 2 * df['BOLL_STD']
        df['BOLL_LOWER'] = df['BOLL_MID'] - 2 * df['BOLL_STD']
        
        # 6. VWAP (成交量加权平均价) - 近似计算(每日)
        # 注意：这是单日VWAP，即成交额/成交量，如果源数据有成交额的话
        if 'amount' in df.columns and 'volume' in df.columns:
             # 避免除以0
             df['VWAP_Day'] = df.apply(lambda x: x['amount'] / x['volume'] if x['volume'] > 0 else x['close'], axis=1)

    except Exception as e:
        print(f"指标计算部分出错: {e}")
        
    return df

def clean_data_robust(df):
    """标准化列名，保留更多有用信息"""
    # 建立映射表
    col_map = {
        '日期': 'trade_date', 'date': 'trade_date',
        '开盘': 'open', 'open': 'open',
        '收盘': 'close', 'close': 'close',
        '最高': 'high', 'high': 'high',
        '最低': 'low', 'low': 'low',
        '成交量': 'volume', 'volume': 'volume',
        '成交额': 'amount', 'amount': 'amount',
        '振幅': 'amplitude', 
        '涨跌幅': 'pct_change', 
        '涨跌额': 'change_amount', 
        '换手率': 'turnover_rate'
    }
    df = df.rename(columns=col_map)
    
    # 格式化日期
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    
    # 强制转数值
    num_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'amplitude', 'pct_change', 'turnover_rate']
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
            
    return df

# ----------------------------------------------------------------------------- 
# 2. 升级版搜索核心 (使用实时行情接口作为索引)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def get_market_maps_pro():
    """
    [核心升级] 使用 ak.stock_zh_a_spot_em() 获取全市场实时行情列表。
    优点：包含所有活跃股票，涵盖 002860、工业富联等，数据最全。
    """
    code2name = {}
    name2code = {}
    try:
        # 获取全市场实时行情 (速度稍慢，但一次加载终身受用)
        df = ak.stock_zh_a_spot_em()
        # 提取代码和名称列 (通常是 '代码' 和 '名称')
        # 兼容不同版本返回的列名
        code_col = '代码' if '代码' in df.columns else 'f12'
        name_col = '名称' if '名称' in df.columns else 'f14'
        
        df[code_col] = df[code_col].astype(str).str.strip()
        df[name_col] = df[name_col].astype(str).str.strip()
        
        code2name = dict(zip(df[code_col], df[name_col]))
        name2code = dict(zip(df[name_col], df[code_col]))
    except Exception as e:
        st.error(f"初始化股票列表失败，请检查网络或akshare版本: {e}")
    
    return code2name, name2code

def smart_search_pro(query, code2name, name2code):
    """
    超级搜索：精准匹配 -> 模糊匹配
    """
    query = str(query).strip()
    
    # 1. 代码精准匹配
    if query in code2name:
        return query, code2name[query], True
    
    # 2. 名称精准匹配
    if query in name2code:
        return name2code[query], query, True
        
    # 3. 名称模糊匹配 (只要包含输入字符就算)
    # 优先匹配以此开头的
    for name, code in name2code.items():
        if query == name: # 双重保险
            return code, name, True
        if query in name:
            return code, name, True
            
    return None, None, False

# ----------------------------------------------------------------------------- 
# 3. 数据获取引擎
# -----------------------------------------------------------------------------
def strategy_em(code, s, e):
    # 东财历史接口，包含最丰富的数据 (振幅、换手、成交额)
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data_robust(df)

def strategy_sina(code, s, e):
    sym = get_symbol_prefix(code)
    df = ak.stock_zh_a_daily(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    # 新浪数据较少，尽量标准化
    return clean_data_robust(df)

@st.cache_data(ttl=300)
def get_stock_data_pro(code, name, days):
    logs = []
    
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    # 优先使用东财，因为字段最全
    strategies = [("EastMoney (全字段)", strategy_em), ("Sina (备用)", strategy_sina)]
    
    df = None
    for src_name, func in strategies:
        try:
            time.sleep(random.uniform(0.1, 0.4))
            temp_df = func(code, s_str, e_str)
            if temp_df is not None and not temp_df.empty:
                df = temp_df
                logs.append(f"✅ 数据来源: {src_name}")
                break
        except: continue
        
    if df is None:
        return None, "无法获取数据，请检查网络连接。", logs
    
    # --- 核心：增加数据丰富度 ---
    # 1. 注入基本信息
    df['code'] = code
    df['name'] = name
    
    # 2. 计算高级指标
    df = add_technical_indicators(df)
    
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. 用户界面 (UI)
# -----------------------------------------------------------------------------
st.sidebar.title("全能股票数据提取")
st.sidebar.caption("🔎 支持 002860 / 工业富联 / 601138 等搜索")
st.sidebar.markdown("---")

# 初始化
with st.spinner("正在连接交易所获取最新股票名录..."):
    code_map, name_map = get_market_maps_pro()

# 输入区
query = st.sidebar.text_input("请输入股票代码或名称", value="002860")
days = st.sidebar.slider("数据回溯天数", 30, 2000, 365)

# 实时搜索反馈
target_code, target_name, found = smart_search_pro(query, code_map, name_map)

if found:
    st.sidebar.success(f"✅ 匹配成功: **{target_name}** ({target_code})")
else:
    if query:
        st.sidebar.error("❌ 未找到该股票，请检查输入")

st.sidebar.markdown("---")

if st.sidebar.button("🚀 获取并生成数据", type="primary", disabled=not found):
    with st.spinner(f"正在深度挖掘 【{target_name}】 的历史与技术数据..."):
        df, err, logs = get_stock_data_pro(target_code, target_name, days)
        
    if err:
        st.error(err)
    else:
        # 成功展示
        st.success(f"数据获取完毕! 共 {len(df)} 条交易记录。")
        
        # 顶部概览
        last = df.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("股票名称", target_name)
        c2.metric("最新收盘", f"{last['close']}")
        
        # 处理可能缺失的涨跌幅
        pct = last.get('pct_change', 0)
        c3.metric("涨跌幅", f"{pct:.2f}%")
        
        # 处理可能缺失的换手率
        to_rate = last.get('turnover_rate', 0)
        c4.metric("换手率", f"{to_rate:.2f}%")
        
        st.markdown("---")
        
        # 1. 下载区域 (文件名修复)
        safe_name = target_name.replace("*", "").replace(":", "").replace("?", "")
        file_time = datetime.datetime.now().strftime("%Y%m%d")
        file_name = f"【{safe_name}_{file_time}】.csv"
        
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label=f"📥 点击下载 CSV (包含 {len(df.columns)} 列数据)",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary"
        )
        st.caption("提示: 下载的文件已包含 MACD, KDJ, RSI, BOLL, 均线, 换手率, 振幅, VWAP 等丰富字段。")
        
        # 2. 数据直接预览 (替代图表)
        st.markdown("### 📋 CSV 数据内容预览")
        st.dataframe(
            df.sort_values('trade_date', ascending=False), 
            use_container_width=True,
            height=500
        )
        
        with st.expander("查看获取日志"):
            st.write(logs)
