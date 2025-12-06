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
st.set_page_config(page_title="猎人指挥中心 V3.3", layout="wide", page_icon="🦅")

# --- 侧边栏 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=800, value=500)

st.sidebar.markdown("---")
st.sidebar.header("🏔️ 筹码底牌 (手动录入)")
# 筹码数据录入
chip_profit = st.sidebar.number_input("获利比例 (%)", min_value=0.0, max_value=100.0, value=85.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)
chip_conc_70 = st.sidebar.number_input("70% 筹码集中度 (%)", value=15.0)
chip_conc_90 = st.sidebar.number_input("90% 筹码集中度 (%)", value=30.0)

# ==========================================
# 2. 核心情报系统 (重构版)
# ==========================================

@st.cache_data(ttl=3600)
def get_stock_name_and_info(code):
    """
    定点爆破：只获取这一只股票的基本信息 (名字、市值、行业)
    避开容易被墙的 'spot' 大表接口
    """
    try:
        # 接口：个股信息查询
        df = ak.stock_individual_info_em(symbol=code)
        # 将表格转为字典方便取值
        info_dict = dict(zip(df['item'], df['value']))
        
        return {
            "name": info_dict.get('股票简称', code),
            "industry": info_dict.get('行业', '未知'),
            "mkt_cap": info_dict.get('总市值', 0), # 单位可能是元
            "pe": info_dict.get('市盈率', '--')
        }
    except:
        # 如果失败，启动B计划：尝试从代码表反查名字
        try:
            names = ak.stock_info_a_code_name()
            name = names[names['code'] == code]['name'].values[0]
            return {"name": name, "industry": "--", "mkt_cap": 0, "pe": "--"}
        except:
            return {"name": f"Code {code}", "industry": "--", "mkt_cap": 0, "pe": "--"}

@st.cache_data(ttl=3600)
def get_restricted_shares(code):
    """获取解禁 (保持不变)"""
    try:
        df = ak.stock_restricted_release_queue_em() 
        df = df[df['code'] == code]
        if df.empty: return "无近期解禁"
        
        today = datetime.datetime.now()
        future_risk = []
        for index, row in df.iterrows():
            date_obj = pd.to_datetime(row['date'])
            if today < date_obj < today + datetime.timedelta(days=30):
                future_risk.append(f"⚠️ {row['date'].strftime('%Y-%m-%d')} 解禁 {row['ratio']}%")
        return " | ".join(future_risk) if future_risk else "未来30天无解禁 (安全)"
    except:
        return "解禁查询超时"

@st.cache_data(ttl=60)
def get_kline_and_metrics(code, days):
    """获取K线，并从K线中提取最新的换手率和价格"""
    try:
        end_str = datetime.datetime.now().strftime("%Y%m%d")
        start_date_obj = datetime.datetime.now() - datetime.timedelta(days=days*1.5)
        start_str = start_date_obj.strftime("%Y%m%d")
        
        # 使用东财历史行情接口 (包含换手率)
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        
        if df is None or df.empty:
            return None, "K线数据为空"

        # 标准化列名
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume', '换手率':'Turnover', '涨跌幅':'PctChg'})
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 计算均线
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA250'] = df['Close'].rolling(250).mean()
        
        # MACD
        exp12 = df['Close'].ewm(span=12, adjust=False).mean()
        exp26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp12 - exp26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])

        # --- 关键步骤：从K线最后一行提取实时数据 ---
        latest = df.iloc[-1]
        
        metrics = {
            "current_price": latest['Close'],
            "change_pct": latest['PctChg'],
            "turnover": latest['Turnover'],  # 这里的换手率非常准确
            "volume": latest['Volume']
        }
        
        return df.tail(days), metrics

    except Exception as e:
        return None, f"数据源连接失败: {str(e)}"

# --- CSV生成 ---
def create_csv(df, basic_info, metrics, user_input, risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人作战情报包 V3.3 ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 基础情报 ---\n")
    output.write(f"名称,{basic_info['name']} ({stock_code})\n")
    output.write(f"行业,{basic_info['industry']}\n")
    output.write(f"现价,{metrics['current_price']}\n")
    output.write(f"涨跌幅,{metrics['change_pct']}%\n")
    output.write(f"换手率,{metrics['turnover']}%\n")
    output.write(f"总市值,{basic_info['mkt_cap']}\n")
    output.write(f"风险排查,{risk}\n")

    output.write("\n--- 🏔️ 筹码底牌 (人工录入) ---\n")
    output.write(f"获利比例,{user_input['profit']}%\n")
    output.write(f"平均成本,{user_input['cost']}\n")
    output.write(f"70%集中度,{user_input['conc70']}%\n")
    output.write(f"90%集中度,{user_input['conc90']}%\n")
    
    output.write("\n--- 📈 历史数据流 ---\n")
    df.to_csv(output, index=False)
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 3. 主界面逻辑
# ==========================================
if stock_code:
    # 1. 获取名字和基本面 (独立接口)
    basic_info = get_stock_name_and_info(stock_code)
    
    # 2. 获取K线和行情数据 (独立接口)
    df, metrics_or_error = get_kline_and_metrics(stock_code, lookback_days)
    
    # 3. 获取解禁风险
    risk_info = get_restricted_shares(stock_code)

    if df is not None:
        metrics = metrics_or_error # 解包
        
        # --- 标题区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"{basic_info['name']} ({stock_code})")
            st.caption(f"行业: {basic_info['industry']} | 市盈率: {basic_info['pe']}")
        with c2:
            color = "red" if metrics['change_pct'] > 0 else "green"
            st.markdown(f"## <span style='color:{color}'>{metrics['current_price']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{metrics['change_pct']}%**")

        # --- 核心指标区 ---
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("换手率 (活跃度)", f"{metrics['turnover']}%")
        
        # 处理市值显示 (如果是数字则格式化)
        mkt_val = basic_info['mkt_cap']
        if isinstance(mkt_val, (int, float)):
            i2.metric("总市值", f"{round(mkt_val/100000000, 2)}亿")
        else:
            i2.metric("总市值", str(mkt_val))
            
        i3.metric("风险排查", "有雷" if "解禁" in risk_info else "安全")
        i4.metric("平均成本", f"{avg_cost}元")

        # 风险提示条
        if "解禁" in risk_info:
            st.error(f"💣 **{risk_info}**")
        else:
            st.success("🛡️ 未来30天无解禁风险")

        # --- 下载按钮 ---
        user_input = {"profit": chip_profit, "cost": avg_cost, "conc70": chip_conc_70, "conc90": chip_conc_90}
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{basic_info['name']}_{stock_code}_{now_str}.csv"
        
        csv_data = create_csv(df, basic_info, metrics, user_input, risk_info)
        
        st.download_button(
            label=f"📥 下载【{basic_info['name']}】情报包",
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
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], line=dict(color='orange', width=1.5), name='年线'), row=1, col=1)
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error(f"❌ 数据获取失败: {metrics_or_error}")
