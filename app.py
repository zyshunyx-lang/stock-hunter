import streamlit as st
import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io
import time

# ==========================================
# 1. 页面配置
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V3.5 (强搜版)", layout="wide", page_icon="🦅")

# --- 侧边栏 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=800, value=500)

st.sidebar.markdown("---")
st.sidebar.header("🕵️‍♂️ 人工安检 (必填)")
chip_profit = st.sidebar.number_input("获利比例 (%)", min_value=0.0, max_value=100.0, value=85.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)
chip_conc_70 = st.sidebar.number_input("70% 集中度 (%)", value=15.0)
chip_conc_90 = st.sidebar.number_input("90% 集中度 (%)", value=30.0)

risk_status = st.sidebar.radio("未来30天解禁/减持：", ("✅ 安全", "⚠️ 有风险"), index=0)
risk_detail = st.sidebar.text_input("风险备注", value="")

# ==========================================
# 2. 核心情报系统 (多源采集)
# ==========================================

@st.cache_data(ttl=3600)
def get_deep_financials(code):
    """
    【核心升级】：强力抓取财务家底
    尝试源1：东财个股资料
    尝试源2：同花顺财务摘要 (包含净资产、公积金等)
    """
    financial_data = {}
    logs = []
    
    # --- 源1：东财个股资料 (最全) ---
    try:
        df_em = ak.stock_individual_info_em(symbol=code)
        for index, row in df_em.iterrows():
            # 过滤掉太长的无关文本
            if len(str(row['value'])) < 50:
                financial_data[row['item']] = row['value']
        logs.append("✅ 东财资料抓取成功")
    except Exception as e:
        logs.append(f"❌ 东财资料失败: {str(e)}")

    # --- 源2：同花顺财务摘要 (补漏神器) ---
    # 如果源1失败，或者缺关键数据，这个能救命
    try:
        df_ths = ak.stock_financial_abstract(symbol=code)
        # 取最近一期的数据
        if not df_ths.empty:
            latest = df_ths.iloc[0] # 通常第一行是最新
            # 强制补充你点名的指标
            financial_data["每股净资产"] = latest.get("每股净资产", "--")
            financial_data["每股公积金"] = latest.get("每股公积金", "--")
            financial_data["每股未分配利润"] = latest.get("每股未分配利润", "--")
            financial_data["净利润"] = latest.get("净利润", "--")
            financial_data["营业收入"] = latest.get("营业收入", "--")
            logs.append("✅ 同花顺财务抓取成功")
    except Exception as e:
        logs.append(f"❌ 同花顺财务失败: {str(e)}")

    # 如果还是空的，填入默认值防止CSV空白
    if not financial_data:
        financial_data["状态"] = "所有数据源均被拦截，请尝试本地运行"
    
    return financial_data, logs

@st.cache_data(ttl=60)
def get_market_data(code, days):
    """获取K线和实时行情"""
    try:
        end_str = datetime.datetime.now().strftime("%Y%m%d")
        start_str = (datetime.datetime.now() - datetime.timedelta(days=days*1.5)).strftime("%Y%m%d")
        
        # 1. K线 (东财)
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume', '换手率':'Turnover', '涨跌幅':'PctChg'})
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 计算指标
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA250'] = df['Close'].rolling(250).mean()
        
        exp12 = df['Close'].ewm(span=12, adjust=False).mean()
        exp26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp12 - exp26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])
        
        # 2. 实时名称和价格
        latest = df.iloc[-1]
        try:
            # 尝试从个股信息接口拿名字
            info = ak.stock_individual_info_em(symbol=code)
            name = info[info['item'] == '股票简称']['value'].values[0]
        except:
            name = f"Code {code}"
            
        base_info = {
            "代码": code,
            "名称": name,
            "现价": latest['Close'],
            "涨跌幅": f"{latest['PctChg']}%",
            "换手率": f"{latest['Turnover']}%",
            "最新成交量": latest['Volume']
        }
        
        return df.tail(days), base_info

    except Exception as e:
        return None, str(e)

# --- CSV生成 ---
def create_full_csv(df, base_info, fin_info, user_chip, user_risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人重装情报包 V3.5 ===\n")
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

    output.write("\n--- 🏢 公司家底 (深度财务) ---\n")
    # 优先写入你关心的几个指标
    priority_keys = ["行业", "总市值", "总股本", "每股净资产", "每股公积金", "每股未分配利润", "净资产收益率", "销售毛利率"]
    for key in priority_keys:
        if key in fin_info:
            output.write(f"{key},{fin_info[key]}\n")
            
    output.write("\n[其他财务指标]\n")
    for k, v in fin_info.items():
        if k not in priority_keys:
            # 清洗逗号和换行
            clean_v = str(v).replace(",", "").replace("\n", " ")
            output.write(f"{k},{clean_v}\n")
    
    output.write("\n--- 📈 K线数据流 ---\n")
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 3. 主界面
# ==========================================
if stock_code:
    # 1. 获取行情
    res = get_market_data(stock_code, lookback_days)
    
    if res and res[0] is not None:
        df, base_info = res
        
        # 2. 获取深度财务 (带状态日志)
        with st.spinner('📡 正在深度挖掘公司家底...'):
            fin_info, logs = get_deep_financials(stock_code)
        
        # --- 标题区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"{base_info['名称']} ({stock_code})")
            # 尝试显示行业
            industry = fin_info.get('行业', '未知')
            st.caption(f"所属行业: {industry}")
            
            # 显示数据抓取日志 (方便调试)
            with st.expander("数据源状态检测"):
                for log in logs:
                    if "❌" in log:
                        st.error(log)
                    else:
                        st.success(log)

        with c2:
            color = "red" if float(base_info['涨跌幅'].strip('%')) > 0 else "green"
            st.markdown(f"## <span style='color:{color}'>{base_info['现价']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{base_info['涨跌幅']}**")

        # --- 核心指标看板 (你关心的家底) ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("每股净资产", fin_info.get("每股净资产", "--"))
        m2.metric("每股公积金", fin_info.get("每股公积金", "--"))
        m3.metric("未分配利润", fin_info.get("每股未分配利润", "--"))
        m4.metric("毛利率", fin_info.get("销售毛利率", "--"))

        # --- 下载按钮 ---
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{base_info['名称']}_{stock_code}_{now_str}.csv"
        
        user_chip = {"profit": chip_profit, "cost": avg_cost, "conc70": chip_conc_70, "conc90": chip_conc_90}
        user_risk = {"status": risk_status, "detail": risk_detail}
        
        csv_data = create_full_csv(df, base_info, fin_info, user_chip, user_risk)
        
        st.download_button(
            label=f"📥 下载【{base_info['名称']}】全息情报包",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )

        # --- 图表 ---
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("K线与均线", "成交量"))
        fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K线'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], line=dict(color='orange', width=1.5), name='年线'), row=1, col=1)
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error(f"❌ 数据获取失败: {res}")
