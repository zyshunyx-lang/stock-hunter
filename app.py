import streamlit as st
import akshare as ak
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io

# ==========================================
# 1. 页面配置
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V5.0 (核聚变版)", layout="wide", page_icon="🦅")

# --- 侧边栏 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
# 筹码计算需要足够长的历史数据，建议至少2年以上
lookback_days = st.sidebar.slider("K线回看天数 (计算筹码需要长周期)", min_value=300, max_value=1000, value=600)

st.sidebar.markdown("---")
st.sidebar.success("🤖 筹码分布已升级为【全自动算法计算】")
st.sidebar.info("不再需要手动录入！系统将根据历史换手率，模拟计算主力的持仓成本。")

# 风险排查 (这个还得留着，因为机器看不懂公告)
risk_status = st.sidebar.radio("未来30天解禁/减持(人工确认)：", ("✅ 安全", "⚠️ 有风险"), index=0)
risk_detail = st.sidebar.text_input("风险备注", value="")

# ==========================================
# 2. 核心算法：筹码分布 (CYQ) 移植版
# ==========================================

def calc_chip_distribution(df):
    """
    【核心核武器】：根据历史K线模拟计算筹码分布
    算法原理：基于换手率的筹码衰减模型
    """
    # 1. 准备数据：我们需要价格(Close)和换手率(Turnover)
    # 如果接口没返回换手率，我们大概估算：成交量/流通股本 (这里简化处理，直接假设Turnover存在或模拟)
    if 'Turnover' not in df.columns:
        # 如果没有换手率，暂时用成交量/假设的一个大盘子模拟，或者直接跳过
        # 为了演示，我们假设每日换手率 = Volume / 1000000 (这只是个权宜之计，实战中Akshare数据通常带换手率)
        df['Turnover'] = df['Volume'] / 10000000 # 假设一亿股本
    
    # 筹码分布容器：{价格: 比例}
    # 为了性能，我们将价格分档（比如每 0.1 元一档）
    chip_dict = {} 
    
    # 开始模拟：从第一天走到最后一天
    for index, row in df.iterrows():
        price = round(row['Close'], 2) # 当前收盘价
        turnover = row['Turnover'] / 100 # 换手率 (百分比转小数)
        
        # 限制换手率在合理范围 (0.1% - 100%)
        if turnover <= 0: turnover = 0.001
        if turnover > 1: turnover = 1.0
        
        # 1. 历史筹码衰减：手里的筹码会被卖掉一部分
        # 卖出的比例 = 当日换手率
        for p in list(chip_dict.keys()):
            chip_dict[p] = chip_dict[p] * (1 - turnover)
            
        # 2. 新筹码生成：当日买入的人，成本就是当日收盘价
        # 新增的筹码比例 = 当日换手率
        if price in chip_dict:
            chip_dict[price] += turnover
        else:
            chip_dict[price] = turnover
            
    # --- 计算统计指标 ---
    # 将字典转为DataFrame方便计算
    chips = pd.DataFrame(list(chip_dict.items()), columns=['Price', 'Volume'])
    chips = chips.sort_values('Price')
    
    total_volume = chips['Volume'].sum()
    if total_volume == 0: return None
    
    chips['CumVolume'] = chips['Volume'].cumsum()
    chips['CumPercent'] = chips['CumVolume'] / total_volume
    
    current_price = df.iloc[-1]['Close']
    
    # 1. 获利比例 (Profit Ratio)
    # 计算所有成本 < 当前价格的筹码比例
    profit_chips = chips[chips['Price'] < current_price]['Volume'].sum()
    profit_ratio = (profit_chips / total_volume) * 100
    
    # 2. 平均成本 (Average Cost)
    avg_cost = (chips['Price'] * chips['Volume']).sum() / total_volume
    
    # 3. 筹码集中度 (Concentration)
    # 找到 90% 的筹码区间 (5% - 95%)
    p05 = chips[chips['CumPercent'] >= 0.05].iloc[0]['Price']
    p95 = chips[chips['CumPercent'] >= 0.95].iloc[0]['Price']
    conc_90 = (p95 - p05) / (p95 + p05) * 100
    
    # 找到 70% 的筹码区间 (15% - 85%)
    p15 = chips[chips['CumPercent'] >= 0.15].iloc[0]['Price']
    p85 = chips[chips['CumPercent'] >= 0.85].iloc[0]['Price']
    conc_70 = (p85 - p15) / (p85 + p15) * 100
    
    return {
        "profit_ratio": round(profit_ratio, 2),
        "avg_cost": round(avg_cost, 2),
        "conc_90": round(conc_90, 2),
        "conc_70": round(conc_70, 2),
        "chip_data": chips # 用于画图
    }

# ==========================================
# 3. 核心情报系统
# ==========================================

@st.cache_data(ttl=3600)
def get_deep_financials(code):
    """财务抓取"""
    financial_data = {}
    try:
        df_em = ak.stock_individual_info_em(symbol=code)
        for index, row in df_em.iterrows():
            financial_data[row['item']] = row['value']
    except: pass
    
    # 补充同花顺
    try:
        df_ths = ak.stock_financial_abstract(symbol=code)
        if not df_ths.empty:
            latest = df_ths.iloc[0]
            financial_data["每股净资产"] = latest.get("每股净资产", "--")
            financial_data["每股公积金"] = latest.get("每股公积金", "--")
            financial_data["每股未分配利润"] = latest.get("每股未分配利润", "--")
            financial_data["销售毛利率"] = latest.get("销售毛利率", "--")
    except: pass
    return financial_data

@st.cache_data(ttl=60)
def get_market_data(code, days):
    try:
        # 使用东财接口获取K线 (包含换手率)
        end_str = datetime.datetime.now().strftime("%Y%m%d")
        start_str = (datetime.datetime.now() - datetime.timedelta(days=days*1.5)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume', '换手率':'Turnover', '涨跌幅':'PctChg'})
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 均线
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
        
        # --- 调用核武器：计算筹码 ---
        # 必须把NaN去掉，否则计算不准
        df_calc = df.dropna(subset=['Close', 'Turnover'])
        chip_info = calc_chip_distribution(df_calc)
        
        # 获取基本信息
        latest = df.iloc[-1]
        try:
            info_em = ak.stock_individual_info_em(symbol=code)
            name = info_em[info_em['item'] == '股票简称']['value'].values[0]
        except:
            name = code
            
        base_info = {
            "代码": code,
            "名称": name,
            "现价": latest['Close'],
            "涨跌幅": f"{latest['PctChg']}%",
            "换手率": f"{latest['Turnover']}%",
            "筹码情报": chip_info
        }
        
        return df.tail(days), base_info

    except Exception as e:
        return None, str(e)

# --- CSV生成 (包含自动算出的筹码数据) ---
def create_full_csv(df, base_info, fin_info, user_risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人核聚变情报包 V5.0 (全自动版) ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 实时行情 ---\n")
    output.write(f"名称,{base_info['名称']}\n")
    output.write(f"现价,{base_info['现价']}\n")
    output.write(f"涨跌幅,{base_info['涨跌幅']}\n")
    
    # 写入自动算出的筹码
    chip = base_info['筹码情报']
    output.write("\n--- 🤖 AI算出的底牌 (精准算法) ---\n")
    if chip:
        output.write(f"获利比例,{chip['profit_ratio']}%\n")
        output.write(f"平均成本,{chip['avg_cost']}\n")
        output.write(f"70%集中度,{chip['conc_70']}%\n")
        output.write(f"90%集中度,{chip['conc_90']}%\n")
    else:
        output.write("筹码计算失败 (数据不足)\n")

    output.write("\n--- 🕵️‍♂️ 人工安检 ---\n")
    output.write(f"风险判定,{user_risk['status']}\n")
    output.write(f"备注,{user_risk['detail']}\n")

    output.write("\n--- 🏢 公司家底 ---\n")
    priority_keys = ["行业", "总市值", "总股本", "每股净资产", "每股公积金", "每股未分配利润", "销售毛利率"]
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
    # 1. 获取行情 + 自动计算筹码
    with st.spinner('🤖 AI正在回溯历史交易，计算主力成本...'):
        res = get_market_data(stock_code, lookback_days)
    
    if res and res[0] is not None:
        df, base_info = res
        fin_info = get_deep_financials(stock_code)
        
        # --- 标题区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"{base_info['名称']} ({stock_code})")
            st.caption(f"行业: {fin_info.get('行业', '--')} | 市值: {fin_info.get('总市值', '--')}")
        with c2:
            color = "red" if float(base_info['涨跌幅'].strip('%')) > 0 else "green"
            st.markdown(f"## <span style='color:{color}'>{base_info['现价']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{base_info['涨跌幅']}**")

        # --- 🔥 核心：全自动筹码看板 ---
        st.markdown("### 🤖 AI 计算的筹码底牌")
        chip = base_info['筹码情报']
        if chip:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("获利比例 (红盘)", f"{chip['profit_ratio']}%", help="大于80%为极强")
            k2.metric("平均成本", f"{chip['avg_cost']}")
            k3.metric("70%集中度", f"{chip['conc_70']}%", help="越小越好，小于15%为高度密集")
            k4.metric("90%集中度", f"{chip['conc_90']}%")
            
            # 简单的筹码分布可视化 (Plotly)
            chip_df = chip['chip_data']
            # 过滤掉量太小的，为了画图快
            chip_df = chip_df[chip_df['Volume'] > 0.001]
            fig_chip = go.Figure()
            # 画一个横向柱状图模拟筹码峰
            fig_chip.add_trace(go.Bar(
                y=chip_df['Price'], x=chip_df['Volume'], 
                orientation='h', 
                marker_color=['red' if p < base_info['现价'] else 'green' for p in chip_df['Price']],
                name='筹码分布'
            ))
            fig_chip.update_layout(title="筹码分布模拟图 (红=获利, 绿=套牢)", height=400, yaxis_title="价格", xaxis_title="筹码量")
            # 加一条现价线
            fig_chip.add_hline(y=base_info['现价'], line_dash="dash", line_color="black", annotation_text="现价")
            st.plotly_chart(fig_chip, use_container_width=True)
        else:
            st.warning("筹码计算失败，可能历史数据不足")

        # --- 下载按钮 ---
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{base_info['名称']}_{stock_code}_{now_str}_V5.csv"
        user_risk = {"status": risk_status, "detail": risk_detail}
        csv_data = create_full_csv(df, base_info, fin_info, user_risk)
        
        st.download_button(
            label=f"📥 下载【{base_info['名称']}】全自动情报包",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )

        # --- 传统K线图 ---
        st.markdown("### 📈 价格趋势")
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("K线", "成交量"))
        fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K线'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error(f"❌ 数据获取失败: {res}")
