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
    page_title="Hunter Data Fetcher",
    page_icon="💾",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 辅助函数
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
# 2. 数据获取核心 (5端口轮询 - 仅保留数据获取)
# -----------------------------------------------------------------------------
def clean_data(df, col_map):
    """清洗数据并统一列名为 Gemini 友好的英文格式"""
    df = df.rename(columns=col_map)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

# 各个数据源策略
def strategy_em(code, s, e):
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    # 映射为英文列名
    return clean_data(df, {
        '日期': 'trade_date', '开盘': 'open', '收盘': 'close', 
        '最高': 'high', '最低': 'low', '成交量': 'volume', 
        '换手率': 'turnover', '涨跌幅': 'pct_change'
    })

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

# 主获取函数
@st.cache_data(ttl=600)
def get_stock_data(code, days):
    data_bundle = {}
    logs = []
    
    # 1. 获取基本面信息 (名称、行业、市值)
    fin_info = {}
    try:
        df_info = ak.stock_individual_info_em(symbol=code)
        fin_info = dict(zip(df_info['item'], df_info['value']))
        logs.append("✅ 基本面数据获取成功")
    except:
        logs.append("⚠️ 基本面数据获取失败")
    
    data_bundle['financial'] = fin_info

    # 2. 获取历史行情 (轮询)
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str = start_dt.strftime("%Y%m%d")
    e_str = end_dt.strftime("%Y%m%d")
    
    strategies = [
        ("EastMoney", strategy_em),
        ("Sina", strategy_sina),
        ("Tencent", strategy_tencent),
        ("Fallback", lambda c,s,e: clean_data(ak.stock_zh_a_hist(symbol=c, period="daily", start_date=s, end_date=e, adjust=""), 
                                              {'日期':'trade_date', '开盘':'open', '收盘':'close', '最高':'high', '最低':'low', '成交量':'volume'}))
    ]
    
    df = None
    source_used = "None"
    
    for name, func in strategies:
        try:
            time.sleep(random.uniform(0.3, 0.8))
            temp_df = func(code, s_str, e_str)
            if temp_df is not None and not temp_df.empty:
                df = temp_df
                source_used = name
                logs.append(f"✅ 行情数据源: {name}")
                break
        except:
            continue
            
    if df is None:
        return None, "所有接口均无法获取数据，请检查代码或稍后重试。", logs
    
    # 3. 数据清洗与指标计算
    # 补全 MACD
    for ma in [5, 20, 60]:
        df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
    df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
    
    # 补全元数据列 (方便 Gemini 读取 CSV 时知道这是哪个股票)
    df['symbol'] = code
    df['name'] = fin_info.get('股票简称', code)
    df['industry'] = fin_info.get('行业', '-')
    
    # 补全换手率 (如果缺失)
    if 'turnover' not in df.columns:
        mcap = fin_info.get('流通股本')
        if mcap:
            df['turnover'] = (df['volume'] / float(mcap)) * 100
        else:
            df['turnover'] = 0.0
            
    data_bundle['history'] = df
    data_bundle['source'] = source_used
    
    # 4. 实时摘要
    try:
        last = df.iloc[-1]
        pct = last.get('pct_change', 0)
        # 如果接口没返回涨跌幅，手动计算
        if pct == 0 and len(df) > 1:
            prev = df.iloc[-2]['close']
            pct = (last['close'] - prev) / prev * 100
            
        data_bundle['realtime'] = {
            'price': last['close'],
            'pct': pct,
            'date': last['trade_date'].strftime("%Y-%m-%d")
        }
    except:
        data_bundle['realtime'] = {'price': 0, 'pct': 0, 'date': '-'}
        
    return data_bundle, None, logs

# ----------------------------------------------------------------------------- 
# 3. 用户界面 (UI)
# -----------------------------------------------------------------------------
st.sidebar.title("数据下载器 (Gemini版)")
st.sidebar.caption("专门用于提取清洗后的数据")
st.sidebar.markdown("---")

# 输入区
input_code = st.sidebar.text_input("股票代码", value="603777")
lookback = st.sidebar.slider("回溯天数", 30, 730, 365)

st.sidebar.markdown("### ✍️ 手动补充信息")
st.sidebar.caption("以下信息将写入CSV供Gemini分析")
manual_avg = st.sidebar.number_input("主力/平均成本 (元)", value=0.0, step=0.1)
manual_note = st.sidebar.text_area("筹码/分析备注", placeholder="例如：底部筹码集中，上方套牢盘较少...")

if st.sidebar.button("获取数据", type="primary"):
    with st.spinner("正在从多源接口拉取数据..."):
        data, err, logs = get_stock_data(input_code, lookback)
        
    if err:
        st.error(err)
        with st.expander("错误日志"):
            st.write(logs)
    else:
        df = data['history']
        rt = data['realtime']
        
        # 将用户手动输入的信息合并到 DataFrame
        # 这样 Gemini 读取 CSV 时，每一行都能看到这些关键上下文
        if manual_avg > 0:
            df['manual_avg_cost'] = manual_avg
        if manual_note:
            df['manual_note'] = manual_note
            
        # 顶部指标
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("股票名称", f"{df.iloc[0]['name']} ({input_code})")
        color = "red" if rt['pct'] > 0 else "green"
        c2.markdown(f"#### 现价: <span style='color:{color}'>{rt['price']:.2f}</span>", unsafe_allow_html=True)
        c3.markdown(f"#### 涨跌: <span style='color:{color}'>{rt['pct']:.2f}%</span>", unsafe_allow_html=True)
        c4.metric("数据来源", data['source'])

        st.markdown("---")
        
        # 下载区 (最重要)
        st.markdown("### 📥 数据下载")
        st.info("提示：下载后的 CSV 包含所有技术指标（MACD, MA）和你的手动备注，可以直接上传给 Gemini 进行分析。")
        
        # 生成 CSV
        csv = df.to_csv(index=False).encode('utf-8-sig')
        file_name = f"{input_code}_{rt['date']}_GeminiData.csv"
        
        col_dl1, col_dl2 = st.columns([1, 4])
        with col_dl1:
            st.download_button(
                label="⬇️ 下载 CSV 文件",
                data=csv,
                file_name=file_name,
                mime="text/csv",
                type="primary"
            )
        
        # 图表预览
        st.markdown("### 📊 K线预览")
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df['trade_date'], open=df['open'], high=df['high'], 
            low=df['low'], close=df['close'], name='K线'
        ))
        
        # 绘制均线
        for ma, color in zip([20, 60], ['purple', 'blue']):
            if f'MA{ma}' in df.columns:
                fig.add_trace(go.Scatter(x=df['trade_date'], y=df[f'MA{ma}'], line=dict(color=color, width=1), name=f'MA{ma}'))
        
        # 如果有手动输入的成本价，画一条线
        if manual_avg > 0:
            fig.add_hline(y=manual_avg, line_dash="dash", line_color="orange", annotation_text="你的成本标记")
            
        fig.update_layout(height=500, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=20, b=0))
        st.plotly_chart(fig, use_container_width=True)

        # 数据预览
        with st.expander("查看原始数据表"):
            st.dataframe(df.sort_values('trade_date', ascending=False), use_container_width=True)
