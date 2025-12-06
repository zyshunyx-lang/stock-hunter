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
    page_title="Hunter V8.8 (Lite)",
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
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------- 
# 1. Helper Functions
# -----------------------------------------------------------------------------
def get_beijing_time():
    utc_now = datetime.datetime.now(pytz.utc)
    return utc_now.astimezone(pytz.timezone('Asia/Shanghai'))

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
    if 'turnover_ratio' not in df.columns:
        df['turnover_ratio'] = 1.0 
    else:
        df['turnover_ratio'] = df['turnover_ratio'].fillna(1.0)
    
    for index, row in df.iterrows():
        price = round(row['close'], decimals)
        turnover = row['turnover_ratio'] / 100.0
        
        for p in list(chip_dict.keys()):
            chip_dict[p] = chip_dict[p] * (1.0 - turnover)
        
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
# 3. Data Fetching (Akshare) - 3个月限制版
# -----------------------------------------------------------------------------
@st.cache_data(ttl=600)
def get_full_data(code, days):
    data_bundle = {}
    
    # 强制计算最近N天的日期范围
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    
    start_date_str = start_dt.strftime("%Y%m%d")
    end_date_str = end_dt.strftime("%Y%m%d")

    # 带随机延迟的重试机制
    def fetch_with_retry(func, retries=3, **kwargs):
        for i in range(retries):
            try:
                # 每次重试前随机等待 1-3 秒，避免请求过于密集
                if i > 0: time.sleep(random.uniform(1, 3))
                return func(**kwargs)
            except Exception as e:
                if i == retries - 1: raise e
        return None

    # --- K Line --- 
    try:
        df = fetch_with_retry(
            ak.stock_zh_a_hist,
            retries=3,
            symbol=code,
            period="daily",
            start_date=start_date_str,
            end_date=end_date_str,
            adjust="qfq"
        )
        
        if df is None or df.empty:
            return None, "未获取到数据，请检查股票代码是否正确。"
            
        rename_map = {
            '日期': 'trade_date', '开盘': 'open', '最高': 'high', '最低': 'low',
            '收盘': 'close', '成交量': 'volume', '换手率': 'turnover_ratio', '涨跌幅': 'pct_change'
        }
        df = df.rename(columns=rename_map)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        cols = ['open', 'high', 'low', 'close', 'volume', 'turnover_ratio', 'pct_change']
        for c in cols:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
                
        for ma in [5, 20, 60]:
            df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
        df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
        data_bundle['history'] = df
        
    except Exception as e:
        return None, f"历史行情获取失败: {str(e)}"

    # --- Chips --- 
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

    # --- Financial --- 
    try:
        # 个股信息也容易超时，给个短超时保护
        info_df = fetch_with_retry(ak.stock_individual_info_em, retries=2, symbol=code)
        info_dict = dict(zip(info_df['item'], info_df['value']))
        data_bundle['financial'] = info_dict
    except:
        data_bundle['financial'] = {}

    # --- Realtime --- 
    try:
        last_row = df.iloc[-1]
        data_bundle['realtime'] = {
            'short_name': data_bundle['financial'].get('股票简称', code),
            'price': last_row['close'],
            'change_pct': last_row.get('pct_change', 0.0)
        }
    except Exception as e:
        data_bundle['realtime'] = {'error': str(e)}
        
    return data_bundle, None

# ----------------------------------------------------------------------------- 
# 4. Main UI
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter V8.8")
st.sidebar.caption("3-Month Safe Mode")
st.sidebar.markdown("---")

input_code = st.sidebar.text_input("股票代码 (6位)", value="603777")

# [重点修改] 默认值设为 90天，最大值限制在 180天
lookback_days = st.sidebar.slider("回溯天数 (最近3个月最佳)", 30, 180, 90)

st.sidebar.markdown("### 风险标记")
risk_check = st.sidebar.radio("风险状态", ["安全", "风险"], index=0)
risk_notes = st.sidebar.text_area("备注", placeholder="在此输入笔记...")

if st.sidebar.button("启动分析", type="primary"):
    with st.spinner(f'正在获取最近 {lookback_days} 天的数据...'):
        data, err = get_full_data(input_code, lookback_days)
    
    if err:
        st.error(f"❌ {err}")
        st.warning("提示：如果频繁出现连接中断，请稍后重试，或检查本地网络是否可以访问东方财富。")
    else:
        hist_df = data['history']
        rt_data = data['realtime']
        fin_data = data['financial']
        chip_metrics = data['chip_metrics']
        chip_dist_df = data['chip_data']
        
        # Header
        name = rt_data.get('short_name', input_code)
        price = rt_data.get('price', '-')
        pct_change = rt_data.get('change_pct', 0)
        
        try:
            val = float(pct_change)
            color = "red" if val > 0 else ("green" if val < 0 else "black")
        except:
            color = "black"

        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        with c1: st.metric("名称", f"{name}")
        with c2: st.markdown(f"#### 价格: <span style='color:{color}'>{price}</span>", unsafe_allow_html=True)
        with c3: st.markdown(f"#### 涨跌: <span style='color:{color}'>{pct_change}%</span>", unsafe_allow_html=True)
        with c4: st.metric("行业", fin_data.get('行业', '-'))
            
        st.markdown("---")
        
        # Metrics
        m1, m2, m3, m4 = st.columns(4)
        with m1: st.metric("获利比例", f"{chip_metrics.get('profit_ratio', 0):.2f}%")
        with m2: st.metric("平均成本", f"{chip_metrics.get('avg_cost', 0):.2f}")
        with m3: st.metric("市盈率", f"{fin_data.get('市盈率(动)', '-')}")
        with m4:
            mcap = fin_data.get('总市值', '-')
            if isinstance(mcap, (int, float)): mcap = f"{mcap/100000000:.2f}亿"
            st.metric("总市值", f"{mcap}")
            
        # Download
        export_df = hist_df.copy()
        export_df['export_time'] = get_beijing_time()
        export_df['risk_notes'] = risk_notes
        csv = export_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("下载 CSV", csv, f"Stock_{input_code}.csv", "text/csv")
        
        # Plots
        tab1, tab2 = st.tabs(["K线图", "筹码分布"])
        with tab1:
            fig_k = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
            fig_k.add_trace(go.Candlestick(
                x=hist_df['trade_date'],
                open=hist_df['open'], high=hist_df['high'],
                low=hist_df['low'], close=hist_df['close'],
                name='K线'
            ), row=1, col=1)
            
            for ma, color in zip([5, 20, 60], ['orange', 'purple', 'blue']):
                if f'MA{ma}' in hist_df.columns:
                    fig_k.add_trace(go.Scatter(
                        x=hist_df['trade_date'], y=hist_df[f'MA{ma}'],
                        mode='lines', name=f'MA{ma}', line=dict(color=color, width=1)
                    ), row=1, col=1)
            
            vol_colors = ['red' if r['close'] >= r['open'] else 'green' for i, r in hist_df.iterrows()]
            fig_k.add_trace(go.Bar(
                x=hist_df['trade_date'], y=hist_df['volume'],
                name='成交量', marker_color=vol_colors
            ), row=2, col=1)
            
            fig_k.update_layout(xaxis_rangeslider_visible=False, height=600, margin=dict(t=10, b=0, l=0, r=0))
            st.plotly_chart(fig_k, use_container_width=True)
            
        with tab2:
            if not chip_dist_df.empty:
                cur_p = float(price) if price != '-' else 0
                chip_prof = chip_dist_df[chip_dist_df['price'] <= cur_p]
                chip_loss = chip_dist_df[chip_dist_df['price'] > cur_p]
                
                fig_chip = go.Figure()
                fig_chip.add_trace(go.Bar(
                    y=chip_prof['price'], x=chip_prof['volume'],
                    orientation='h', name='获利盘', marker_color='red', opacity=0.6
                ))
                fig_chip.add_trace(go.Bar(
                    y=chip_loss['price'], x=chip_loss['volume'],
                    orientation='h', name='套牢盘', marker_color='green', opacity=0.6
                ))
                fig_chip.add_hline(y=cur_p, line_dash="dash", annotation_text=f"当前价: {cur_p}")
                fig_chip.update_layout(
                    title=f"筹码分布 - {name}",
                    height=600, bargap=0.0, hovermode="y unified",
                    xaxis_title="筹码占比", yaxis_title="价格"
                )
                st.plotly_chart(fig_chip, use_container_width=True)
            else:
                st.info("数据不足，无法计算筹码分布。")
