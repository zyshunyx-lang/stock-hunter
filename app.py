import streamlit as st
import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io

# ==========================================
# 1. 页面配置
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V3.4 (重装版)", layout="wide", page_icon="🦅")

# --- 侧边栏：目标锁定 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=800, value=500)

st.sidebar.markdown("---")
st.sidebar.header("🕵️‍♂️ 人工安检通道 (必填)")

# 1. 筹码数据 (你的强项)
st.sidebar.caption("1. 筹码底牌 (看手机填)")
chip_profit = st.sidebar.number_input("获利比例 (%)", min_value=0.0, max_value=100.0, value=85.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)
chip_conc_70 = st.sidebar.number_input("70% 筹码集中度 (%)", value=15.0)
chip_conc_90 = st.sidebar.number_input("90% 筹码集中度 (%)", value=30.0)

# 2. 风险排查 (你的决定)
st.sidebar.caption("2. 风险定性 (看F10填)")
risk_status = st.sidebar.radio(
    "未来30天解禁/减持情况：",
    ("✅ 安全 (无解禁/无减持)", "⚠️ 有风险 (有解禁/减持/利空)"),
    index=0
)
risk_detail = st.sidebar.text_input("风险备注 (选填，如：12.10解禁20亿)", value="")

# ==========================================
# 2. 核心情报系统 (深度抓取版)
# ==========================================

@st.cache_data(ttl=300)
def get_comprehensive_info(code):
    """
    饱和式抓取：获取东方财富该个股的【全部】基本面指标
    不再只取几个数，而是把整个表都扒下来
    """
    try:
        # 接口：个股信息查询 (这个接口非常全，包含财务、估值、股本等)
        df = ak.stock_individual_info_em(symbol=code)
        # 转换为字典
        info_dict = dict(zip(df['item'], df['value']))
        return info_dict
    except:
        return {}

def fetch_kline_robust(code, days):
    """双引擎K线获取 (东财 + 新浪备用)"""
    end_str = datetime.datetime.now().strftime("%Y%m%d")
    start_date_obj = datetime.datetime.now() - datetime.timedelta(days=days*1.5)
    start_str = start_date_obj.strftime("%Y%m%d")
    
    # 尝试引擎 A (东财)
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume', '换手率':'Turnover', '涨跌幅':'PctChg'})
        source = "东财数据源"
    except:
        # 尝试引擎 B (新浪)
        try:
            sina_symbol = f"sh{code}" if code.startswith('6') else f"sz{code}"
            df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_str, end_date=end_str, adjust="qfq")
            df = df.rename(columns={'date':'Date', 'open':'Open', 'close':'Close', 'high':'High', 'low':'Low', 'volume':'Volume'})
            df['Turnover'] = 0 # 新浪不带换手率，暂时置0
            df['PctChg'] = 0   # 新浪不带涨跌幅
            source = "新浪备用源"
        except Exception as e:
            return None, str(e)

    # 计算指标
    df['Date'] = pd.to_datetime(df['Date'])
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['MA120'] = df['Close'].rolling(120).mean()
    df['MA250'] = df['Close'].rolling(250).mean()
    
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIF'] = exp12 - exp26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = 2 * (df['DIF'] - df['DEA'])
    
    return df, source

# --- CSV生成 (包含所有抓取到的数据) ---
def create_full_intelligence_file(df, full_info, user_chip, user_risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人重装情报包 V3.4 ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🕵️‍♂️ 人工安检结果 ---\n")
    output.write(f"安全状态,{user_risk['status']}\n")
    output.write(f"风险备注,{user_risk['detail']}\n")
    
    output.write("\n--- 🏔️ 筹码底牌 (人工录入) ---\n")
    output.write(f"获利比例,{user_chip['profit']}%\n")
    output.write(f"平均成本,{user_chip['cost']}\n")
    output.write(f"70%集中度,{user_chip['conc70']}%\n")
    output.write(f"90%集中度,{user_chip['conc90']}%\n")

    output.write("\n--- 🏢 公司全息档案 (自动抓取) ---\n")
    # 把抓取到的所有字段都写进去 (不再过滤)
    for k, v in full_info.items():
        # 清洗一下逗号，防止CSV格式错乱
        clean_v = str(v).replace(",", "，")
        output.write(f"{k},{clean_v}\n")
    
    output.write("\n--- 📈 历史量价数据流 ---\n")
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 3. 主界面逻辑
# ==========================================
if stock_code:
    # 1. 获取全量基本面
    with st.spinner('📡 正在全网搜集该公司所有情报...'):
        full_info = get_comprehensive_info(stock_code)
    
    # 2. 获取K线
    df, msg = fetch_kline_robust(stock_code, lookback_days)

    if df is not None:
        # 获取最新即时数据
        latest = df.iloc[-1]
        
        # --- 抬头区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            name = full_info.get('股票简称', stock_code)
            ind = full_info.get('行业', '--')
            st.title(f"{name} ({stock_code})")
            st.caption(f"所属行业: {ind} | 总市值: {full_info.get('总市值', '--')}")
        with c2:
            price = latest['Close']
            # 兼容新浪源可能没有 PctChg
            pct = latest.get('PctChg', 0)
            color = "red" if pct > 0 else "green"
            st.markdown(f"## <span style='color:{color}'>{price}</span>", unsafe_allow_html=True)
            st.markdown(f"**{pct}%**")

        # --- 核心财务透视 (展示部分重要指标) ---
        with st.expander("📊 核心财务透视 (已全部打包进CSV)", expanded=True):
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("市盈率(动)", full_info.get('市盈率', '--'))
            f2.metric("市净率", full_info.get('市净率', '--'))
            f3.metric("ROE", full_info.get('净资产收益率', '--'))
            f4.metric("毛利率", full_info.get('销售毛利率', '--'))
            
            f5, f6, f7, f8 = st.columns(4)
            f5.metric("总股本", full_info.get('总股本', '--'))
            f6.metric("流通股", full_info.get('流通股', '--'))
            f7.metric("营收增长", full_info.get('营业收入同比增长', '--'))
            f8.metric("净利增长", full_info.get('净利润同比增长', '--'))

        # --- 风险状态栏 ---
        if "有风险" in risk_status:
            st.error(f"💣 **指挥官判定有雷**：{risk_detail if risk_detail else '未填写详情'}")
        else:
            st.success("🛡️ **指挥官判定安全**：无近期解禁/利空")

        # --- 下载区 ---
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{name}_{stock_code}_{now_str}.csv"
        
        user_chip = {"profit": chip_profit, "cost": avg_cost, "conc70": chip_conc_70, "conc90": chip_conc_90}
        user_risk = {"status": risk_status, "detail": risk_detail}
        
        csv_data = create_full_intelligence_file(df, full_info, user_chip, user_risk)
        
        st.download_button(
            label=f"📥 一键下载全息情报包 (.csv)",
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
        st.error(f"数据获取失败: {msg}")
