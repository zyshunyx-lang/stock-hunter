import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io

# ==========================================
# 1. 页面配置
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V7.0 (国际版)", layout="wide", page_icon="🦅")

# --- 侧边栏 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=1000, value=500)

st.sidebar.markdown("---")
st.sidebar.header("🕵️‍♂️ 情报补全 (雅虎查不到的)")
# 雅虎只有行情，没有A股特色的数据，需要你填
turnover_rate = st.sidebar.number_input("换手率 (%)", value=0.0, help="看一眼软件填入")
total_mv = st.sidebar.number_input("总市值 (亿)", value=0.0)
chip_profit = st.sidebar.number_input("获利比例 (%)", value=85.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)
chip_conc_70 = st.sidebar.number_input("70% 集中度 (%)", value=15.0)
chip_conc_90 = st.sidebar.number_input("90% 集中度 (%)", value=30.0)

risk_status = st.sidebar.radio("未来30天解禁/减持：", ("✅ 安全", "⚠️ 有风险"), index=0)
risk_detail = st.sidebar.text_input("风险备注", value="")

# ==========================================
# 2. 核心数据获取 (Yahoo Finance)
# ==========================================

@st.cache_data(ttl=60)
def get_data_yfinance(code, days):
    """
    使用雅虎财经接口，专治各种网络不服
    """
    try:
        # 1. 转换代码格式 (A股 -> 雅虎格式)
        # 60xxxx -> 60xxxx.SS (上海)
        # 00xxxx, 30xxxx -> xxxxxx.SZ (深圳)
        if code.startswith('6'):
            symbol = f"{code}.SS"
        else:
            symbol = f"{code}.SZ"
            
        # 2. 获取数据
        ticker = yf.Ticker(symbol)
        
        # 历史K线
        # period='2y' 代表拿2年数据
        df = ticker.history(period="2y")
        
        if df.empty:
            return None, "雅虎返回数据为空，请检查代码"
            
        # 3. 数据清洗
        df = df.reset_index()
        # 雅虎的列名: Date, Open, High, Low, Close, Volume, Dividends, Stock Splits
        df = df.rename(columns={'Date':'Date', 'Open':'Open', 'High':'High', 'Low':'Low', 'Close':'Close', 'Volume':'Volume'})
        
        # 去掉时区信息，防止报错
        df['Date'] = df['Date'].dt.tz_localize(None)
        
        # 截取用户需要的天数
        df = df.tail(days)

        # 4. 计算指标
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA250'] = df['Close'].rolling(250).mean()
        
        exp12 = df['Close'].ewm(span=12, adjust=False).mean()
        exp26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp12 - exp26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])
        
        # 5. 获取实时信息 (雅虎的 info 有时很慢，我们直接用K线最后一行)
        latest = df.iloc[-1]
        # 计算涨跌幅 (今天收盘 - 昨天收盘) / 昨天收盘
        if len(df) > 1:
            prev = df.iloc[-2]['Close']
            curr = latest['Close']
            pct = (curr - prev) / prev * 100
        else:
            pct = 0
            
        base_info = {
            "代码": code,
            "名称": f"Code {code}", # 雅虎中文名支持不好，直接显示代码
            "现价": round(latest['Close'], 2),
            "涨跌幅": f"{pct:.2f}%",
            "成交量": latest['Volume']
        }
        
        return df, base_info

    except Exception as e:
        return None, f"雅虎接口报错: {str(e)}"

# --- CSV生成 ---
def create_csv_file(df, base_info, user_inputs):
    output = io.StringIO()
    output.write("=== 🦅 猎人指挥中心 V7.0 (国际版) ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 实时行情 ---\n")
    output.write(f"代码,{base_info['代码']}\n")
    output.write(f"现价,{base_info['现价']}\n")
    output.write(f"涨跌幅,{base_info['涨跌幅']}\n")
    
    output.write("\n--- 🕵️‍♂️ 人工补全情报 ---\n")
    for k, v in user_inputs.items():
        output.write(f"{k},{v}\n")
    
    output.write("\n--- 📈 历史K线数据 ---\n")
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 3. 主界面
# ==========================================
if stock_code:
    with st.spinner('🛰️ 正在通过国际专线连接...'):
        res = get_data_yfinance(stock_code, lookback_days)
    
    if res and res[0] is not None:
        df, base_info = res
        
        # --- 标题区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"股票代码: {stock_code}")
            st.caption("数据源: Yahoo Finance (国际接口)")
        with c2:
            try:
                pct_val = float(base_info['涨跌幅'].replace('%', ''))
                color = "red" if pct_val > 0 else "green"
            except:
                color = "black"
            st.markdown(f"## <span style='color:{color}'>{base_info['现价']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{base_info['涨跌幅']}**")

        # --- 核心指标 (人工填写的) ---
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("换手率 (人工)", f"{turnover_rate}%")
        i2.metric("总市值 (人工)", f"{total_mv}亿")
        i3.metric("获利比例 (人工)", f"{chip_profit}%")
        i4.metric("风险状态", "有雷" if "有风险" in risk_status else "安全")

        # --- 下载按钮 ---
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"Stock_{stock_code}_{now_str}.csv"
        
        user_inputs = {
            "换手率": f"{turnover_rate}%",
            "总市值": f"{total_mv}亿",
            "获利比例": f"{chip_profit}%",
            "平均成本": avg_cost,
            "70%集中度": f"{chip_conc_70}%",
            "风险": f"{risk_status} {risk_detail}"
        }
        
        csv_data = create_csv_file(df, base_info, user_inputs)
        
        st.download_button(
            label="📥 下载情报包 (.csv)",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )

        # --- 图表区 ---
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("K线与均线", "成交量"))
        fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K线'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], line=dict(color='orange', width=1.5), name='MA250'), row=1, col=1)
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error(f"❌ 获取失败: {res[1] if res else '未知错误'}")
