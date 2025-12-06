import streamlit as st
import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io
import requests
import time

# ==========================================
# 1. 页面配置
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V4.0 (iTick版)", layout="wide", page_icon="🦅")

# --- 侧边栏：目标锁定 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=1000, value=500)

# --- 侧边栏：iTick 战略配置 ---
st.sidebar.markdown("---")
st.sidebar.header("🔌 iTick 数据接入")
itick_token = st.sidebar.text_input("iTick API Token (选填)", type="password", help="填入后优先使用iTick高速接口，留空则使用备用源")
# 默认使用CN市场
market_region = st.sidebar.selectbox("市场区域", ["CN (A股)", "HK (港股)", "US (美股)"], index=0)

st.sidebar.markdown("---")
st.sidebar.header("🏔️ 筹码底牌 (手动录入)")
chip_profit = st.sidebar.number_input("获利比例 (%)", min_value=0.0, max_value=100.0, value=85.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)
chip_conc_70 = st.sidebar.number_input("70% 集中度 (%)", value=15.0)
chip_conc_90 = st.sidebar.number_input("90% 集中度 (%)", value=30.0)

# 风险排查
risk_status = st.sidebar.radio("未来30天解禁/减持：", ("✅ 安全", "⚠️ 有风险"), index=0)
risk_detail = st.sidebar.text_input("风险备注", value="")

# ==========================================
# 2. 核心情报系统 (三级火箭架构)
# ==========================================

@st.cache_data(ttl=3600)
def get_deep_financials(code):
    """饱和式抓取财务家底 (东财+同花顺)"""
    financial_data = {}
    logs = []
    try:
        df_em = ak.stock_individual_info_em(symbol=code)
        for index, row in df_em.iterrows():
            if len(str(row['value'])) < 50:
                financial_data[row['item']] = row['value']
        logs.append("✅ 东财资料抓取成功")
    except:
        logs.append("⚠️ 东财资料失败")

    try:
        df_ths = ak.stock_financial_abstract(symbol=code)
        if not df_ths.empty:
            latest = df_ths.iloc[0]
            financial_data["每股净资产"] = latest.get("每股净资产", "--")
            financial_data["每股公积金"] = latest.get("每股公积金", "--")
            financial_data["每股未分配利润"] = latest.get("每股未分配利润", "--")
            financial_data["净利润"] = latest.get("净利润", "--")
            financial_data["营业收入"] = latest.get("营业收入", "--")
            logs.append("✅ 同花顺财务抓取成功")
    except:
        logs.append("⚠️ 同花顺财务失败")
    
    return financial_data, logs

# --- 引擎 1: iTick API ---
def fetch_kline_itick(code, days, token, region_code):
    """
    一级火箭：调用 iTick.org API
    文档参考：https://github.com/itick-org
    """
    if not token:
        return None, "未配置 Token"
    
    try:
        # 转换区域代码
        region = region_code.split()[0] # CN/HK/US
        
        # 构造 URL (kType=8 代表日线)
        url = "https://api.itick.org/stock/kline"
        params = {
            "region": region,
            "code": code,
            "kType": "8", 
            "limit": days,
            "token": token
        }
        
        # 发起请求
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        
        if data.get("code") != 0: # 0 表示成功
            return None, f"iTick 报错: {data.get('msg')}"
            
        # 解析数据
        # iTick 返回格式: [{"t":时间戳, "o":开盘, "c":收盘, "h":最高, "l":最低, "v":成交量, ...}]
        raw_list = data.get("data", [])
        if not raw_list:
            return None, "iTick 返回空数据"
            
        df = pd.DataFrame(raw_list)
        # 重命名列以适配系统
        df = df.rename(columns={
            "t": "Date", "o": "Open", "c": "Close", 
            "h": "High", "l": "Low", "v": "Volume"
        })
        
        # 处理时间戳 (毫秒 -> 日期)
        df['Date'] = pd.to_datetime(df['Date'], unit='ms')
        
        # iTick 可能不返回换手率(turnover)和涨跌幅，需自行计算或后续补全
        if 'turnover' not in df.columns:
            df['Turnover'] = 0 
        if 'pct' not in df.columns:
            df['PctChg'] = df['Close'].pct_change() * 100
            
        return df, "🚀 iTick.org (专业接口)"
        
    except Exception as e:
        return None, f"iTick 连接失败: {str(e)}"

# --- 引擎 2 & 3: Akshare (东财/新浪) ---
def fetch_kline_akshare(code, days):
    """二级/三级火箭：常规爬虫"""
    end_str = datetime.datetime.now().strftime("%Y%m%d")
    start_str = (datetime.datetime.now() - datetime.timedelta(days=days*1.5)).strftime("%Y%m%d")
    
    # 尝试东财
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume', '换手率':'Turnover', '涨跌幅':'PctChg'})
        return df, "🚗 Eastmoney (备用源)"
    except:
        pass
        
    # 尝试新浪
    try:
        sina_symbol = f"sh{code}" if code.startswith('6') else f"sz{code}"
        df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_str, end_date=end_str, adjust="qfq")
        df = df.rename(columns={'date':'Date', 'open':'Open', 'close':'Close', 'high':'High', 'low':'Low', 'volume':'Volume'})
        df['Turnover'] = 0
        df['PctChg'] = df['Close'].pct_change() * 100
        return df, "🚲 Sina (兜底源)"
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=60)
def get_market_data_v4(code, days, token, region):
    """智能调度指挥官"""
    
    # 1. 优先尝试 iTick
    df, source = fetch_kline_itick(code, days, token, region)
    
    # 2. 失败则降级使用 Akshare
    if df is None:
        if token: st.toast(f"iTick 连接失败 ({source})，正在切换备用源...", icon="⚠️")
        df, source = fetch_kline_akshare(code, days)
        
    if df is None:
        return None, f"所有数据源均不可用: {source}"

    # 3. 统一计算指标 (MA, MACD)
    df['Date'] = pd.to_datetime(df['Date'])
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['MA250'] = df['Close'].rolling(250).mean()
    
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIF'] = exp12 - exp26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = 2 * (df['DIF'] - df['DEA'])
    
    # 4. 提取最新行情
    latest = df.iloc[-1]
    # 尝试补全名称
    try:
        info = ak.stock_individual_info_em(symbol=code)
        name = info[info['item'] == '股票简称']['value'].values[0]
    except:
        name = f"Code {code}"

    base_info = {
        "代码": code,
        "名称": name,
        "数据源": source,
        "现价": latest['Close'],
        "涨跌幅": f"{latest['PctChg']:.2f}%" if pd.notnull(latest['PctChg']) else "--",
        "换手率": f"{latest['Turnover']}%" if 'Turnover' in df.columns else "--",
    }
    
    return df.tail(days), base_info

# --- CSV生成 ---
def create_full_csv(df, base_info, fin_info, user_chip, user_risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人重装情报包 V4.0 (iTick版) ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 实时行情 ---\n")
    for k, v in base_info.items():
        output.write(f"{k},{v}\n")
        
    output.write("\n--- 🕵️‍♂️ 人工安检 ---\n")
    output.write(f"风险判定,{user_risk['status']}\n")
    output.write(f"备注,{user_risk['detail']}\n")
    
    output.write("\n--- 🏔️ 筹码底牌 ---\n")
    output.write(f"获利比例,{user_chip['profit']}%\n")
    output.write(f"平均成本,{user_chip['cost']}\n")
    output.write(f"70%集中度,{user_chip['conc70']}%\n")
    output.write(f"90%集中度,{user_chip['conc90']}%\n")

    output.write("\n--- 🏢 公司家底 ---\n")
    priority_keys = ["行业", "总市值", "总股本", "每股净资产", "每股公积金", "每股未分配利润", "净资产收益率", "销售毛利率"]
    for key in priority_keys:
        if key in fin_info:
            output.write(f"{key},{fin_info[key]}\n")
    
    output.write("\n--- 📈 K线数据流 ---\n")
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 3. 主界面
# ==========================================
if stock_code:
    # 1. 获取行情 (三级火箭启动)
    res = get_market_data_v4(stock_code, lookback_days, itick_token, market_region)
    
    if res and res[0] is not None:
        df, base_info = res
        
        # 2. 获取财务
        with st.spinner('📡 深度扫描中...'):
            fin_info, logs = get_deep_financials(stock_code)
        
        # --- 标题区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"{base_info['名称']} ({stock_code})")
            st.caption(f"数据源: {base_info['数据源']} | 行业: {fin_info.get('行业', '--')}")
        with c2:
            try:
                pct_val = float(base_info['涨跌幅'].strip('%'))
                color = "red" if pct_val > 0 else "green"
            except:
                color = "black"
            st.markdown(f"## <span style='color:{color}'>{base_info['现价']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{base_info['涨跌幅']}**")

        # --- 核心指标 ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("换手率", base_info['换手率'])
        m2.metric("市盈率", fin_info.get("市盈率", "--"))
        m3.metric("总市值", fin_info.get("总市值", "--"))
        m4.metric("每股净资产", fin_info.get("每股净资产", "--"))

        # --- 下载按钮 ---
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{base_info['名称']}_{stock_code}_{now_str}.csv"
        user_chip = {"profit": chip_profit, "cost": avg_cost, "conc70": chip_conc_70, "conc90": chip_conc_90}
        user_risk = {"status": risk_status, "detail": risk_detail}
        csv_data = create_full_csv(df, base_info, fin_info, user_chip, user_risk)
        
        st.download_button(
            label=f"📥 下载情报包：{file_name}",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )

        # --- 图表 ---
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("K线", "成交量"))
        fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K线'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], line=dict(color='orange', width=1.5), name='年线'), row=1, col=1)
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error(f"❌ 获取失败: {res[1]}")
