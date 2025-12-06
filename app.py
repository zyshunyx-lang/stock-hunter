import streamlit as st
import pandas as pd
import akshare as ak
import plotly.graph_objects as go
import datetime
import pytz
import time
import random

# ----------------------------------------------------------------------------- 
# 0. 全局配置
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter Data Fetcher (Lite)",
    page_icon="📉",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 核心辅助函数
# -----------------------------------------------------------------------------
def get_symbol_prefix(code):
    """自动补充代码前缀"""
    if code.startswith('6'): return f"sh{code}"
    if code.startswith('0') or code.startswith('3'): return f"sz{code}"
    if code.startswith('8') or code.startswith('4'): return f"bj{code}"
    return code

def calculate_macd(df, short=12, long=26, mid=9):
    """计算 MACD 指标"""
    close = df['close']
    ema12 = close.ewm(span=short, adjust=False).mean()
    ema26 = close.ewm(span=long, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=mid, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif, dea, macd

def clean_data(df, col_map):
    """标准化数据列名"""
    df = df.rename(columns=col_map)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for c in numeric_cols:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

# ----------------------------------------------------------------------------- 
# 2. 名称获取专用逻辑 (解决名称显示问题)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600) # 缓存1小时，避免重复请求
def get_all_stock_names_map():
    """
    获取全市场股票代码-名称映射表。
    相比单独请求个股信息，这种方式虽然第一次慢几秒，但后续查询极其稳定且快。
    """
    try:
        # 接口：获取A股股票代码和简称列表
        df = ak.stock_info_a_code_name()
        # 将代码转换为字符串并统一格式（去除可能的空格）
        df['code'] = df['code'].astype(str).str.strip()
        return dict(zip(df['code'], df['name']))
    except Exception:
        return {}

def get_stock_name_robust(code, name_map):
    """多级保障获取股票名称"""
    # 1. 优先从全市场缓存中查
    if code in name_map:
        return name_map[code]
    
    # 2. 如果缓存没查到（可能是新股），尝试请求个股资料
    try:
        df = ak.stock_individual_info_em(symbol=code)
        info = dict(zip(df['item'], df['value']))
        return info.get('股票简称', code)
    except:
        pass
        
    return code # 实在找不到，返回代码

# ----------------------------------------------------------------------------- 
# 3. 历史行情获取逻辑 (多源轮询)
# -----------------------------------------------------------------------------
def strategy_em(code, s, e):
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data(df, {'日期': 'trade_date', '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low', '成交量': 'volume', '换手率': 'turnover', '涨跌幅': 'pct_change'})

def strategy_sina(code, s, e):
    sym = get_symbol_prefix(code)
    df = ak.stock_zh_a_daily(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data(df, {'date': 'trade_date'})

def strategy_tencent(code, s, e):
    sym = get_symbol_prefix(code)
    df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data(df, {'date': 'trade_date'})

@st.cache_data(ttl=300)
def get_stock_history(code, days):
    logs = []
    
    # 日期计算
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    # 轮询策略
    strategies = [("EastMoney", strategy_em), ("Sina", strategy_sina), ("Tencent", strategy_tencent)]
    
    df = None
    for name, func in strategies:
        try:
            time.sleep(random.uniform(0.1, 0.3))
            temp_df = func(code, s_str, e_str)
            if temp_df is not None and not temp_df.empty:
                df = temp_df
                logs.append(f"✅ 数据源: {name}")
                break
        except: continue
            
    if df is None: 
        return None, "无法获取历史数据，请检查代码或网络。", logs
    
    # 补全指标
    for ma in [5, 10, 20, 60]: 
        df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
    df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
    
    # 补全基本信息列
    df['code'] = code
    
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. 用户界面 (极简版)
# -----------------------------------------------------------------------------
st.sidebar.title("数据获取助手")
st.sidebar.markdown("---")

# 1. 预加载全市场名称映射 (后台运行，静默加载)
with st.spinner("正在初始化股票列表..."):
    name_map = get_all_stock_names_map()

# 2. 输入区
input_code = st.sidebar.text_input("股票代码", value="603777")
lookback = st.sidebar.slider("查询回溯天数", 30, 1000, 365)

# 实时显示名称预览
current_name = get_stock_name_robust(input_code, name_map)
if current_name != input_code:
    st.sidebar.success(f"匹配股票: **{current_name}**")
else:
    st.sidebar.warning("未匹配到名称，请确认代码")

st.sidebar.markdown("---")
# 3. 查询按钮
if st.sidebar.button("开始查询", type="primary"):
    
    if current_name == input_code:
        st.error(f"❌ 无法识别代码 {input_code} 的中文名称，请检查输入。")
    else:
        with st.spinner(f"正在获取 【{current_name}】 的历史数据..."):
            df, err, logs = get_stock_history(input_code, lookback)
        
        if err:
            st.error(err)
        else:
            # 注入名称到 DataFrame
            df['name'] = current_name
            
            # 界面展示
            st.success(f"获取成功: {current_name} ({input_code})")
            
            # 获取最新数据用于展示
            last_row = df.iloc[-1]
            last_date = last_row['trade_date'].strftime("%Y-%m-%d")
            close_price = last_row['close']
            
            # 计算简单的涨跌幅展示
            pct_display = 0.0
            if 'pct_change' in df.columns:
                pct_display = last_row['pct_change']
            elif len(df) > 1:
                prev_close = df.iloc[-2]['close']
                pct_display = (close_price - prev_close) / prev_close * 100
                
            color = "red" if pct_display > 0 else "green"
            
            # 顶部指标栏
            c1, c2, c3 = st.columns(3)
            c1.metric("股票名称", current_name)
            c2.markdown(f"#### 收盘价: <span style='color:{color}'>{close_price}</span>", unsafe_allow_html=True)
            c3.markdown(f"#### 日期: {last_date}", unsafe_allow_html=True)
            
            st.markdown("---")
            
            # --- 关键修改：文件下载 ---
            # 格式: 【股票中文名称_时间】.csv
            # 时间格式建议用 YYYYMMDD，避免冒号等非法字符
            file_time = datetime.datetime.now().strftime("%Y%m%d")
            file_name = f"【{current_name}_{file_time}】.csv"
            
            csv_data = df.to_csv(index=False).encode('utf-8-sig')
            
            st.download_button(
                label=f"📥 下载数据: {file_name}",
                data=csv_data,
                file_name=file_name,
                mime="text/csv",
                type="primary"
            )
            
            # 预览图表
            with st.expander("📊 数据预览", expanded=True):
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=df['trade_date'], open=df['open'], high=df['high'], 
                    low=df['low'], close=df['close'], name='K线'
                ))
                for ma in [20, 60]:
                    if f'MA{ma}' in df:
                        fig.add_trace(go.Scatter(x=df['trade_date'], y=df[f'MA{ma}'], line=dict(width=1), name=f'MA{ma}'))
                
                fig.update_layout(height=450, xaxis_rangeslider_visible=False, title=f"{current_name} K线走势")
                st.plotly_chart(fig, use_container_width=True)
