import streamlit as st
import pandas as pd
import numpy as np
import akshare as ak
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import pytz
import time
import random

# ----------------------------------------------------------------------------- 
# 0. Global Config
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter V9.0 (Multi-Port)",
    page_icon="🏹",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    }
    .status-ok { color: green; font-weight: bold; }
    .status-fail { color: red; }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------- 
# 1. Helper Functions
# -----------------------------------------------------------------------------
def get_beijing_time():
    utc_now = datetime.datetime.now(pytz.utc)
    return utc_now.astimezone(pytz.timezone('Asia/Shanghai'))

def get_symbol_prefix(code):
    """自动判断股票代码前缀 (sh/sz/bj)"""
    if code.startswith('6'): return f"sh{code}"
    if code.startswith('0') or code.startswith('3'): return f"sz{code}"
    if code.startswith('8') or code.startswith('4'): return f"bj{code}"
    return code

def calculate_macd(df, short=12, long=26, mid=9):
    close = df['close']
    ema12 = close.ewm(span=short, adjust=False).mean()
    ema26 = close.ewm(span=long, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=mid, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif, dea, macd

# ----------------------------------------------------------------------------- 
# 2. Chip Distribution Algo
# -----------------------------------------------------------------------------
def calc_chip_distribution(df, decimals=2):
    chip_dict = {} 
    
    # 智能补全换手率：如果缺少换手率数据，默认使用 2.0% (0.02) 作为估算值
    # 在 get_full_data 中我们会尝试用 (成交量/流通股本) 来计算精确值
    if 'turnover_ratio' not in df.columns:
        df['turnover_ratio'] = 2.0 
    else:
        df['turnover_ratio'] = df['turnover_ratio'].fillna(2.0)
    
    for index, row in df.iterrows():
        price = round(row['close'], decimals)
        turnover = row['turnover_ratio'] / 100.0
        
        # 历史筹码衰减
        for p in list(chip_dict.keys()):
            chip_dict[p] = chip_dict[p] * (1.0 - turnover)
        
        # 新增筹码
        if price in chip_dict:
            chip_dict[price] += turnover
        else:
            chip_dict[price] = turnover
            
    chip_df = pd.DataFrame(list(chip_dict.items()), columns=['price', 'volume'])
    chip_df = chip_df.sort_values('price')
    
    total_vol = chip_df['volume'].sum()
    if total_vol > 0:
        chip_df['volume'] = chip_df['volume'] / total_vol
        
    chip_df['cumsum_vol'] = chip_df['volume'].cumsum()
    return chip_df

def get_chip_metrics(chip_df, current_price):
    if chip_df.empty:
        return 0, 0, 0, 0
    profit_df = chip_df[chip_df['price'] <= current_price]
    profit_ratio = profit_df['volume'].sum() * 100
    avg_cost = (chip_df['price'] * chip_df['volume']).sum()
    try:
        p05 = chip_df[chip_df['cumsum_vol'] >= 0.05].iloc[0]['price']
        p95 = chip_df[chip_df['cumsum_vol'] >= 0.95].iloc[0]['price']
        concentration_90 = (p95 - p05) / (p05 + p95) * 2 * 100
    except:
        concentration_90 = 0
    return profit_ratio, avg_cost, concentration_90, chip_df

# ----------------------------------------------------------------------------- 
# 3. Data Fetching Strategies (The 5 Ports)
# -----------------------------------------------------------------------------

# 通用清洗函数
def clean_data(df, col_map):
    df = df.rename(columns=col_map)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

# 策略 1: 东方财富 (EastMoney) - 包含换手率，质量最好
def strategy_em(code, start_date, end_date):
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty Data")
    return clean_data(df, {
        '日期': 'trade_date', '开盘': 'open', '最高': 'high', '最低': 'low', 
        '收盘': 'close', '成交量': 'volume', '换手率': 'turnover_ratio', '涨跌幅': 'pct_change'
    })

# 策略 2: 新浪财经 (Sina) - 稳定，需前缀
def strategy_sina(code, start_date, end_date):
    symbol = get_symbol_prefix(code)
    df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty Data")
    # Sina返回: date, open, high, low, close, volume
    return clean_data(df, {'date': 'trade_date'})

# 策略 3: 腾讯财经 (Tencent) - 需前缀
def strategy_tencent(code, start_date, end_date):
    symbol = get_symbol_prefix(code)
    df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty Data")
    return clean_data(df, {'date': 'trade_date'})

# 策略 4: 网易财经 (NetEase/163) - 历史悠久
def strategy_netease(code, start_date, end_date):
    # 网易通常直接用6位代码，或者特定格式，akshare封装一般已处理
    try:
        df = ak.stock_zh_a_hist_163(symbol=code, start_date=start_date, end_date=end_date)
    except:
        # 尝试带前缀
        symbol = get_symbol_prefix(code)
        df = ak.stock_zh_a_hist_163(symbol=symbol, start_date=start_date, end_date=end_date)
    if df is None or df.empty: raise ValueError("Empty Data")
    return clean_data(df, {'日期': 'trade_date', '收盘价': 'close', '开盘价': 'open', '最高价': 'high', '最低价': 'low', '成交量': 'volume'})

# 策略 5: 备用/实时转历史 (Fallback)
def strategy_fallback(code, start_date, end_date):
    # 如果以上都失败，尝试获取不复权的数据，可能接口不同
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty: raise ValueError("Empty Data")
    return clean_data(df, {
        '日期': 'trade_date', '开盘': 'open', '最高': 'high', '最低': 'low', 
        '收盘': 'close', '成交量': 'volume', '换手率': 'turnover_ratio'
    })

# ----------------------------------------------------------------------------- 
# 4. Main Data Fetcher (The Engine)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=600)
def get_full_data(code, days):
    data_bundle = {}
    logs = []
    
    # 日期计算
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str = start_dt.strftime("%Y%m%d")
    e_str = end_dt.strftime("%Y%m%d")

    # 定义策略列表
    strategies = [
        ("EastMoney (Official)", strategy_em),
        ("Sina Finance", strategy_sina),
        ("Tencent Stock", strategy_tencent),
        ("NetEase (163)", strategy_netease),
        ("EastMoney (Unadjusted)", strategy_fallback)
    ]
    
    df = None
    used_source = "None"
    
    # --- 1. 获取个股信息 (用于补充换手率) ---
    circulating_share = None
    try:
        info_df = ak.stock_individual_info_em(symbol=code)
        info_dict = dict(zip(info_df['item'], info_df['value']))
        data_bundle['financial'] = info_dict
        # 获取流通股本 (用于计算换手率)
        if '流通股本' in info_dict:
            circulating_share = float(info_dict['流通股本'])
    except:
        data_bundle['financial'] = {}
        logs.append("⚠️ Financial info fetch failed.")

    # --- 2. 轮询获取历史行情 (K-Line) ---
    for source_name, strategy_func in strategies:
        try:
            time.sleep(random.uniform(0.5, 1.5)) # 稍微停顿，防止过快
            temp_df = strategy_func(code, s_str, e_str)
            if temp_df is not None and not temp_df.empty:
                df = temp_df
                used_source = source_name
                logs.append(f"✅ Success using {source_name}")
                break
        except Exception as e:
            logs.append(f"❌ {source_name} failed: {str(e)[:50]}...")
            continue
            
    if df is None:
        return None, "All 5 data sources failed. Please check the code or try again later.", logs

    # --- 3. 数据补全与计算 ---
    # 补全 MACD
    for ma in [5, 20, 60]:
        df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
    df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
    
    # 补全换手率 (如果接口没返回)
    if 'turnover_ratio' not in df.columns:
        if circulating_share and circulating_share > 0:
            # 换手率 = 成交量 / 流通股本 * 100
            df['turnover_ratio'] = (df['volume'] / circulating_share) * 100
            logs.append("ℹ️ Calculated turnover_ratio using financial data.")
        else:
            df['turnover_ratio'] = 2.0 # 默认 2%
            logs.append("⚠️ Missing turnover data, using default 2%.")
            
    data_bundle['history'] = df
    data_bundle['source'] = used_source

    # --- 4. 计算筹码 ---
    try:
        chip_raw_df = calc_chip_distribution(df)
        current_price = df.iloc[-1]['close']
        profit_ratio, avg_cost, concentration, chip_final_df = get_chip_metrics(chip_raw_df, current_price)
        data_bundle['chip_metrics'] = {
            'profit_ratio': profit_ratio, 'avg_cost': avg_cost, 'concentration_90': concentration
        }
        data_bundle['chip_data'] = chip_final_df
    except Exception as e:
        data_bundle['chip_metrics'] = {'profit_ratio':0, 'avg_cost':0, 'concentration_90':0}
        data_bundle['chip_data'] = pd.DataFrame()
        logs.append(f"⚠️ Chip calc error: {str(e)}")

    # --- 5. 实时数据 ---
    try:
        last_row = df.iloc[-1]
        # 尝试计算涨跌幅 (如果接口没返回)
        pct = last_row.get('pct_change', 0)
        if pct == 0 and len(df) > 1:
            prev_close = df.iloc[-2]['close']
            pct = ((last_row['close'] - prev_close) / prev_close) * 100
            
        data_bundle['realtime'] = {
            'short_name': data_bundle['financial'].get('股票简称', code),
            'price': last_row['close'],
            'change_pct': round(pct, 2)
        }
    except Exception as e:
        data_bundle['realtime'] = {'error': str(e)}
        
    return data_bundle, None, logs

# ----------------------------------------------------------------------------- 
# 5. Main UI
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter V9.0")
st.sidebar.caption("Multi-Source Failover")
st.sidebar.markdown("---")

input_code = st.sidebar.text_input("Stock Code", value="603777")
lookback_days = st.sidebar.slider("Lookback Days", 30, 365, 120)

if st.sidebar.button("Launch Analysis", type="primary"):
    with st.spinner('Trying 5 different data ports...'):
        data, err, logs = get_full_data(input_code, lookback_days)
    
    # 显示日志
    with st.expander("Connection Logs (Debug)"):
        for log in logs:
            if "Success" in log: st.markdown(f"<span style='color:green'>{log}</span>", unsafe_allow_html=True)
            elif "failed" in log: st.markdown(f"<span style='color:red'>{log}</span>", unsafe_allow_html=True)
            else: st.write(log)

    if err:
        st.error(err)
    else:
        hist_df = data['history']
        rt_data = data['realtime']
        fin_data = data['financial']
        chip_metrics = data['chip_metrics']
        chip_dist_df = data['chip_data']
        source = data['source']
        
        st.success(f"Data fetched successfully via: **{source}**")
        
        # Header
        name = rt_data.get('short_name', input_code)
        price = rt_data.get('price', '-')
        pct_change = rt_data.get('change_pct', 0)
        
        color = "red" if float(pct_change) > 0 else "green"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Name", name)
        c2.markdown(f"#### Price: <span style='color:{color}'>{price}</span>", unsafe_allow_html=True)
        c3.markdown(f"#### Change: <span style='color:{color}'>{pct_change}%</span>", unsafe_allow_html=True)
        c4.metric("Market Cap", f"{float(fin_data.get('总市值', 0))/100000000:.2f}亿" if fin_data.get('总市值') else "-")
            
        st.markdown("---")
        
        # Dashboard
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Profit Ratio", f"{chip_metrics['profit_ratio']:.2f}%")
        m2.metric("Avg Cost", f"{chip_metrics['avg_cost']:.2f}")
        m3.metric("Concentration (90%)", f"{chip_metrics['concentration_90']:.2f}%")
        m4.metric("Source", source)
            
        # Tabs
        tab1, tab2 = st.tabs(["K-Line Chart", "Chip Distribution"])
        with tab1:
            fig_k = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
            fig_k.add_trace(go.Candlestick(x=hist_df['trade_date'], open=hist_df['open'], high=hist_df['high'], low=hist_df['low'], close=hist_df['close'], name='K'), row=1, col=1)
            for ma in [5, 20, 60]:
                if f'MA{ma}' in hist_df.columns:
                    fig_k.add_trace(go.Scatter(x=hist_df['trade_date'], y=hist_df[f'MA{ma}'], mode='lines', name=f'MA{ma}'), row=1, col=1)
            fig_k.add_trace(go.Bar(x=hist_df['trade_date'], y=hist_df['volume'], name='Vol'), row=2, col=1)
            fig_k.update_layout(height=600, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig_k, use_container_width=True)
            
        with tab2:
            if not chip_dist_df.empty:
                cur_p = float(price) if price != '-' else 0
                prof = chip_dist_df[chip_dist_df['price'] <= cur_p]
                loss = chip_dist_df[chip_dist_df['price'] > cur_p]
                fig_c = go.Figure()
                fig_c.add_trace(go.Bar(y=prof['price'], x=prof['volume'], orientation='h', name='Profit', marker_color='red'))
                fig_c.add_trace(go.Bar(y=loss['price'], x=loss['volume'], orientation='h', name='Loss', marker_color='green'))
                fig_c.add_hline(y=cur_p, line_dash="dash", annotation_text=f"Current: {cur_p}")
                fig_c.update_layout(height=600, bargap=0, title=f"Chip Distribution ({name})")
                st.plotly_chart(fig_c, use_container_width=True)
