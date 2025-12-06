import streamlit as st
import adata
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io

# ==========================================
# 1. 页面配置
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V6.0 (adata版)", layout="wide", page_icon="🦅")

# --- 侧边栏 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=200, max_value=1000, value=500)

st.sidebar.markdown("---")
st.sidebar.success("✅ 数据源已切换为 adata (多源融合/抗干扰)")
st.sidebar.info("自动筹码算法已就绪，无需手动录入。")

# 风险排查 (adata 目前主要专注行情，公告类数据较少，保留人工通道)
risk_status = st.sidebar.radio("未来30天解禁/减持(人工确认)：", ("✅ 安全", "⚠️ 有风险"), index=0)
risk_detail = st.sidebar.text_input("风险备注", value="")

# ==========================================
# 2. 核心算法：筹码分布 (CYQ)
# ==========================================
def calc_chip_distribution(df):
    """
    根据历史K线和换手率，模拟计算筹码分布
    """
    # adata返回的列名通常是: trade_date, open, close, low, high, volume, amount, turnover_ratio
    # 我们需要标准化列名
    if 'turnover_ratio' not in df.columns:
        # 如果没有换手率，尝试用成交量模拟 (粗略)
        df['turnover_ratio'] = 1.0 # 默认值，防止报错
        
    chip_dict = {} 
    
    for index, row in df.iterrows():
        price = round(row['close'], 2)
        turnover = row['turnover_ratio'] / 100 
        
        if turnover <= 0: turnover = 0.001
        if turnover > 1: turnover = 1.0
        
        # 历史筹码衰减
        for p in list(chip_dict.keys()):
            chip_dict[p] = chip_dict[p] * (1 - turnover)
            
        # 新增筹码
        if price in chip_dict:
            chip_dict[price] += turnover
        else:
            chip_dict[price] = turnover
            
    # 统计指标
    chips = pd.DataFrame(list(chip_dict.items()), columns=['Price', 'Volume'])
    chips = chips.sort_values('Price')
    total_volume = chips['Volume'].sum()
    
    if total_volume == 0: return None
    
    chips['CumVolume'] = chips['Volume'].cumsum()
    chips['CumPercent'] = chips['CumVolume'] / total_volume
    current_price = df.iloc[-1]['close']
    
    # 获利比例
    profit_chips = chips[chips['Price'] < current_price]['Volume'].sum()
    profit_ratio = (profit_chips / total_volume) * 100
    
    # 平均成本
    avg_cost = (chips['Price'] * chips['Volume']).sum() / total_volume
    
    # 集中度
    try:
        p05 = chips[chips['CumPercent'] >= 0.05].iloc[0]['Price']
        p95 = chips[chips['CumPercent'] >= 0.95].iloc[0]['Price']
        conc_90 = (p95 - p05) / (p95 + p05) * 100
        
        p15 = chips[chips['CumPercent'] >= 0.15].iloc[0]['Price']
        p85 = chips[chips['CumPercent'] >= 0.85].iloc[0]['Price']
        conc_70 = (p85 - p15) / (p85 + p15) * 100
    except:
        conc_90 = 0
        conc_70 = 0
    
    return {
        "profit_ratio": round(profit_ratio, 2),
        "avg_cost": round(avg_cost, 2),
        "conc_90": round(conc_90, 2),
        "conc_70": round(conc_70, 2),
        "chip_data": chips
    }

# ==========================================
# 3. 数据获取层 (adata 驱动)
# ==========================================

@st.cache_data(ttl=60)
def get_market_data_adata(code, days):
    try:
        # 1. 获取历史K线 (adata 自动融合多源)
        start_date = (datetime.datetime.now() - datetime.timedelta(days=days*1.5)).strftime("%Y-%m-%d")
        # k_type=1 (日线)
        df = adata.stock.market.get_market(stock_code=code, start_date=start_date, k_type=1)
        
        if df is None or df.empty:
            return None, "adata 返回 K线数据为空"

        # 标准化列名以适配后续计算
        # adata返回: stock_code, trade_time, trade_date, open, close, high, low, volume, amount, turnover_ratio
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date')
        
        # 2. 计算均线 & MACD
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['MA60'] = df['close'].rolling(60).mean()
        df['MA250'] = df['close'].rolling(250).mean()
        
        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp12 - exp26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])
        
        # 3. 计算筹码
        chip_info = calc_chip_distribution(df)
        
        # 4. 获取实时行情 (Snaphot)
        # adata.stock.market.list_market_current 实际上可以取到最新价
        current_df = adata.stock.market.list_market_current(stock_code=code)
        
        if not current_df.empty:
            curr = current_df.iloc[0]
            base_info = {
                "代码": code,
                "名称": curr.get('short_name', code),
                "现价": curr.get('price', df.iloc[-1]['close']),
                "涨跌幅": f"{curr.get('change_pct', 0)}%",
                "换手率": f"{curr.get('turnover_ratio', 0)}%",
                "成交量": curr.get('volume', 0),
                "总市值": f"{round(curr.get('total_market_value', 0)/1e8, 2)}亿" if 'total_market_value' in curr else "--"
            }
        else:
            # 兜底：如果实时取不到，用K线最后一行
            last = df.iloc[-1]
            base_info = {
                "代码": code,
                "名称": code, # adata K线不带名字
                "现价": last['close'],
                "涨跌幅": "--", # K线里不一定有当天实时的涨跌幅
                "换手率": f"{last['turnover_ratio']}%",
                "总市值": "--" 
            }
            
        return df.tail(days), base_info, chip_info

    except Exception as e:
        return None, f"adata 运行异常: {str(e)}"

# ==========================================
# 4. CSV生成器
# ==========================================
def create_full_csv(df, base_info, chip_info, user_risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人指挥中心 V6.0 (adata版) ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 实时行情 ---\n")
    for k, v in base_info.items():
        output.write(f"{k},{v}\n")
        
    output.write("\n--- 🤖 AI算出的底牌 (adata驱动) ---\n")
    if chip_info:
        output.write(f"获利比例,{chip_info['profit_ratio']}%\n")
        output.write(f"平均成本,{chip_info['avg_cost']}\n")
        output.write(f"70%集中度,{chip_info['conc_70']}%\n")
        output.write(f"90%集中度,{chip_info['conc_90']}%\n")
    
    output.write("\n--- 🕵️‍♂️ 风险排查 ---\n")
    output.write(f"风险状态,{user_risk['status']}\n")
    output.write(f"备注,{user_risk['detail']}\n")
    
    output.write("\n--- 📈 历史数据流 ---\n")
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 5. 主界面逻辑
# ==========================================
if stock_code:
    with st.spinner('📡 adata 正在从多源聚合数据...'):
        res = get_market_data_adata(stock_code, lookback_days)
    
    if res and res[0] is not None:
        df, base_info, chip_info = res
        
        # --- 标题区 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"{base_info['名称']} ({stock_code})")
            st.caption(f"总市值: {base_info['总市值']}")
        with c2:
            try:
                pct = float(base_info['涨跌幅'].replace('%', ''))
                color = "red" if pct > 0 else "green"
            except:
                color = "black"
            st.markdown(f"## <span style='color:{color}'>{base_info['现价']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{base_info['涨跌幅']}**")

        # --- 筹码看板 ---
        st.markdown("### 🤖 筹码分布 (AI自动计算)")
        if chip_info:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("获利比例", f"{chip_info['profit_ratio']}%")
            k2.metric("平均成本", f"{chip_info['avg_cost']}")
            k3.metric("70%集中度", f"{chip_info['conc_70']}%")
            k4.metric("90%集中度", f"{chip_info['conc_90']}%")
            
            # 筹码可视化
            chip_df = chip_info['chip_data']
            chip_df = chip_df[chip_df['Volume'] > 0.001]
            fig_chip = go.Figure()
            fig_chip.add_trace(go.Bar(
                y=chip_df['Price'], x=chip_df['Volume'], 
                orientation='h', 
                marker_color=['red' if p < float(base_info['现价']) else 'green' for p in chip_df['Price']],
                name='筹码'
            ))
            fig_chip.update_layout(title="筹码分布模拟图", height=300, margin=dict(l=10, r=10, t=30, b=10))
            fig_chip.add_hline(y=float(base_info['现价']), line_dash="dash", line_color="black")
            st.plotly_chart(fig_chip, use_container_width=True)

        # --- 五档盘口 (adata 特色功能) ---
        with st.expander("📊 查看实时五档盘口 (adata直连)", expanded=False):
            try:
                # 获取五档行情
                five_df = adata.stock.market.get_market_five(stock_code=stock_code)
                if not five_df.empty:
                    st.dataframe(five_df)
                else:
                    st.warning("暂无五档盘口数据 (可能是非交易时间)")
            except:
                st.warning("五档行情连接超时")

        # --- 下载按钮 ---
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{base_info['名称']}_{stock_code}_{now_str}.csv"
        user_risk = {"status": risk_status, "detail": risk_detail}
        csv_data = create_full_csv(df, base_info, chip_info, user_risk)
        
        st.download_button(
            label=f"📥 下载情报包 (.csv)",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )

        # --- K线图表 ---
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("K线", "成交量"))
        fig.add_trace(go.Candlestick(x=df['trade_date'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='K线'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['trade_date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['trade_date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['trade_date'], y=df['MA250'], line=dict(color='orange', width=1.5), name='MA250'), row=1, col=1)
        fig.add_trace(go.Bar(x=df['trade_date'], y=df['volume'], name='成交量'), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error(f"❌ 获取失败: {base_info}")
