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
    page_title="Hunter Data Fetcher Pro",
    page_icon="🏹",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 辅助与清洗函数
# -----------------------------------------------------------------------------
def get_symbol_prefix(code):
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

def clean_data(df, col_map):
    df = df.rename(columns=col_map)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for c in numeric_cols:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

# ----------------------------------------------------------------------------- 
# 2. 数据获取逻辑 (5端口轮询)
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

@st.cache_data(ttl=600)
def get_stock_data(code, days):
    data_bundle = {}
    logs = []
    
    # 1. 获取基本面 (名称)
    fin_info = {'name': code} # 默认值
    try:
        df_info = ak.stock_individual_info_em(symbol=code)
        info_dict = dict(zip(df_info['item'], df_info['value']))
        fin_info['name'] = info_dict.get('股票简称', code)
        fin_info['industry'] = info_dict.get('行业', '-')
        fin_info['mcap'] = info_dict.get('流通股本', None)
        logs.append(f"✅ 获取名称成功: {fin_info['name']}")
    except:
        logs.append("⚠️ 无法获取股票名称")
    
    data_bundle['financial'] = fin_info

    # 2. 获取行情
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    strategies = [("EastMoney", strategy_em), ("Sina", strategy_sina), ("Tencent", strategy_tencent)]
    
    df = None
    for name, func in strategies:
        try:
            time.sleep(random.uniform(0.1, 0.5))
            temp_df = func(code, s_str, e_str)
            if temp_df is not None and not temp_df.empty:
                df = temp_df
                logs.append(f"✅ 行情源: {name}")
                break
        except: continue
            
    if df is None: return None, "无法获取数据，请检查代码。", logs
    
    # 3. 补全指标
    for ma in [5, 10, 20, 60]: df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
    df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
    
    # 补全元数据
    df['code'] = code
    df['name'] = fin_info['name']
    
    # 补全换手率
    if 'turnover' not in df.columns:
        mcap = fin_info.get('mcap')
        df['turnover'] = (df['volume'] / float(mcap) * 100) if mcap else 0.0

    data_bundle['history'] = df
    
    # 实时摘要
    try:
        last = df.iloc[-1]
        pct = last.get('pct_change', 0)
        if pct == 0 and len(df)>1: pct = (last['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close'] * 100
        data_bundle['realtime'] = {'price': last['close'], 'pct': pct, 'date': last['trade_date'].strftime("%Y-%m-%d")}
    except:
        data_bundle['realtime'] = {'price': 0, 'pct': 0, 'date': '-'}
        
    return data_bundle, None, logs

# ----------------------------------------------------------------------------- 
# 3. 侧边栏界面 (输入区)
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter Pro (Gemini专用)")
st.sidebar.markdown("---")

# 基础设置
c1, c2 = st.sidebar.columns([1, 1])
input_code = c1.text_input("代码", "603777")
lookback = c2.number_input("回溯天数", 30, 1000, 365)

# 尝试预加载名称（提升体验）
if len(input_code) == 6:
    st.sidebar.caption(f"当前分析对象: {input_code}")

st.sidebar.markdown("### ✍️ 筹码分布手动录入")
st.sidebar.info("以下数据将作为【全时段参考值】写入文件，供Gemini判断主力状态。")

# 分组1：整体持仓
with st.sidebar.expander("1. 整体持仓与获利", expanded=True):
    col_h1, col_h2 = st.columns(2)
    hold_min = col_h1.number_input("持仓区间-低", 0.0, 1000.0, 6.90, step=0.1)
    hold_max = col_h2.number_input("持仓区间-高", 0.0, 1000.0, 20.50, step=0.1)
    profit_pct = st.number_input("获利持仓占比 (%)", 0.0, 100.0, 82.44, step=0.01)

# 分组2：90%筹码
with st.sidebar.expander("2. 90% 筹码分布", expanded=True):
    col_90_1, col_90_2 = st.columns(2)
    chip90_min = col_90_1.number_input("90%区间-低", 0.0, 1000.0, 8.40, step=0.1)
    chip90_max = col_90_2.number_input("90%区间-高", 0.0, 1000.0, 15.90, step=0.1)
    conc90 = st.number_input("90% 集中度", 0.0, 100.0, 30.86, step=0.01)

# 分组3：70%筹码
with st.sidebar.expander("3. 70% 筹码分布", expanded=True):
    col_70_1, col_70_2 = st.columns(2)
    chip70_min = col_70_1.number_input("70%区间-低", 0.0, 1000.0, 9.30, step=0.1)
    chip70_max = col_70_2.number_input("70%区间-高", 0.0, 1000.0, 15.70, step=0.1)
    conc70 = st.number_input("70% 集中度", 0.0, 100.0, 25.60, step=0.01)

avg_cost = st.sidebar.number_input("平均/主力成本 (元)", value=0.0)

# ----------------------------------------------------------------------------- 
# 4. 主逻辑区
# -----------------------------------------------------------------------------
if st.button("生成分析文件", type="primary"):
    with st.spinner(f"正在获取 {input_code} 数据..."):
        data, err, logs = get_stock_data(input_code, lookback)
    
    if err:
        st.error(err)
    else:
        df = data['history']
        rt = data['realtime']
        stock_name = data['financial']['name']
        
        # --- 注入手动数据 (关键步骤) ---
        # 我们添加前缀 REF_ (Reference) 让 Gemini 知道这是参考数据
        df['REF_Holding_Range'] = f"{hold_min}-{hold_max}"
        df['REF_Profit_Ratio'] = profit_pct
        df['REF_Cost90_Range'] = f"{chip90_min}-{chip90_max}"
        df['REF_Conc90'] = conc90
        df['REF_Cost70_Range'] = f"{chip70_min}-{chip70_max}"
        df['REF_Conc70'] = conc70
        
        if avg_cost > 0:
            df['REF_Avg_Cost'] = avg_cost
            
        # 增加一列提示，专门给 Gemini 看
        df['GEMINI_NOTE'] = "Columns starting with 'REF_' are STATIC manual inputs representing the chip distribution state at the end of period. They apply to the whole dataset."

        # --- 界面展示 ---
        st.success(f"数据获取成功: {stock_name}")
        
        # 顶部指标
        k1, k2, k3 = st.columns(3)
        color = "red" if rt['pct'] > 0 else "green"
        k1.metric("股票名称", f"{stock_name}")
        k2.markdown(f"#### 现价: <span style='color:{color}'>{rt['price']}</span>", unsafe_allow_html=True)
        k3.markdown(f"#### 涨幅: <span style='color:{color}'>{rt['pct']:.2f}%</span>", unsafe_allow_html=True)
        
        st.markdown("---")

        # --- 下载功能 ---
        # 文件名格式: 【股票名称+时间】.csv
        file_time = datetime.datetime.now().strftime("%Y%m%d")
        file_name = f"【{stock_name}_{file_time}】.csv"
        csv_data = df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label=f"📥 下载分析文件: {file_name}",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary"
        )
        
        st.info("💡 提示：此文件已包含你录入的所有筹码参数。请直接上传给 Gemini，并提示它‘参考 REF_ 开头的列进行筹码分析’。")

        # --- 简单预览 ---
        with st.expander("📊 K线预览"):
            fig = go.Figure()
            fig.add_trace(go.Candlestick(x=df['trade_date'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='K线'))
            for ma in [20, 60]: 
                if f'MA{ma}' in df: fig.add_trace(go.Scatter(x=df['trade_date'], y=df[f'MA{ma}'], line=dict(width=1), name=f'MA{ma}'))
            
            # 画出筹码区间辅助线 (如果合理)
            if chip90_min > 0 and chip90_max > 0:
                fig.add_hrect(y0=chip90_min, y1=chip90_max, line_width=0, fillcolor="red", opacity=0.1, annotation_text="90%筹码区")
            
            fig.update_layout(height=400, xaxis_rangeslider_visible=False, margin=dict(t=0, b=0, l=0, r=0))
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("查看数据样本"):
            # 只显示最后几行，让用户确认手动数据已注入
            cols_to_show = ['trade_date', 'close', 'REF_Profit_Ratio', 'REF_Conc90', 'GEMINI_NOTE']
            st.dataframe(df[cols_to_show].tail(3), use_container_width=True)
