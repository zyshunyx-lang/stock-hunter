import streamlit as st
import adata
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
st.set_page_config(page_title="猎人指挥中心 V8.2", layout="wide", page_icon="🦅")

# --- 侧边栏 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=200, max_value=1000, value=500)

st.sidebar.markdown("---")
# 自动检测环境提示
try:
    # 尝试连接东财测试
    ak.stock_zh_a_spot_em()
    env_status = "🚀 本地/国内高速网络"
except:
    env_status = "☁️ 云端/海外网络 (已自动切换备用源)"
st.sidebar.success(f"网络环境: {env_status}")

# 风险排查人工确认
risk_status = st.sidebar.radio("人工风险确认：", ("✅ 安全", "⚠️ 有风险"), index=0)
risk_detail = st.sidebar.text_input("风险备注", value="")

# ==========================================
# 2. 核心算法：筹码分布 (自动计算)
# ==========================================
def calc_chip_distribution(df):
    """
    全自动筹码算法：基于历史换手率计算成本分布
    """
    # 确保有换手率数据
    if 'turnover_ratio' not in df.columns:
        # 如果没有换手率(如新浪源)，用成交量粗略模拟
        df['turnover_ratio'] = 1.0 
        
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
            
    # 统计
    chips = pd.DataFrame(list(chip_dict.items()), columns=['Price', 'Volume'])
    chips = chips.sort_values('Price')
    total_volume = chips['Volume'].sum()
    
    if total_volume == 0: return None
    
    chips['CumVolume'] = chips['Volume'].cumsum()
    chips['CumPercent'] = chips['CumVolume'] / total_volume
    current_price = df.iloc[-1]['close']
    
    # 核心指标
    profit_chips = chips[chips['Price'] < current_price]['Volume'].sum()
    profit_ratio = (profit_chips / total_volume) * 100
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
# 3. 数据获取 (智能路由)
# ==========================================

@st.cache_data(ttl=300)
def get_full_data(code, days):
    """
    智能获取数据：
    1. 优先尝试 adata (东财源)
    2. 失败则降级 akshare (新浪源)
    """
    try:
        # --- 1. 获取行情 ---
        start_date = (datetime.datetime.now() - datetime.timedelta(days=days*1.5)).strftime("%Y-%m-%d")
        
        # 尝试 Adata (首选)
        try:
            df = adata.stock.market.get_market(stock_code=code, start_date=start_date, k_type=1)
            source = "Adata (东财)"
        except:
            df = pd.DataFrame()

        # 如果 Adata 失败或为空，尝试 Akshare 新浪源 (备用)
        if df.empty:
            sina_symbol = f"sh{code}" if code.startswith('6') else f"sz{code}"
            df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date.replace("-",""), adjust="qfq")
            # 标准化列名
            df = df.rename(columns={'date':'trade_date', 'open':'open', 'high':'high', 'low':'low', 'close':'close', 'volume':'volume'})
            df['turnover_ratio'] = 0 # 新浪不带换手率
            source = "Akshare (新浪)"
        
        if df is None or df.empty: return None, "所有数据源均连接失败"
        
        # 数据清洗
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date')
        
        # 指标计算
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['MA60'] = df['close'].rolling(60).mean()
        df['MA250'] = df['close'].rolling(250).mean()
        
        # --- 2. 自动计算筹码 ---
        chip_info = calc_chip_distribution(df)
        
        # --- 3. 获取实时信息 ---
        # 尝试获取最新行情
        latest = df.iloc[-1]
        try:
            # 尝试抓取名字
            info_em = ak.stock_individual_info_em(symbol=code)
            name = info_em[info_em['item'] == '股票简称']['value'].values[0]
        except:
            name = f"Code {code}"
            
        base_info = {
            "名称": name,
            "现价": latest['close'],
            "涨跌": "--", # 历史K线难算当日实时涨跌
            "换手": f"{latest['turnover_ratio']}%",
            "数据源": source
        }

        # --- 4. 获取深度财务 (尽力而为) ---
        fin_info = {}
        try:
            info_em = ak.stock_individual_info_em(symbol=code)
            for _, row in info_em.iterrows():
                fin_info[row['item']] = row['value']
        except:
            fin_info = {"行业": "数据获取受限"}
            
        return df.tail(days), base_info, chip_info, fin_info

    except Exception as e:
        return None, str(e)

# --- CSV 生成 ---
def create_csv(df, base, chip, fin, user_risk):
    output = io.StringIO()
    output.write("=== 🦅 猎人指挥中心 V8.2 (最终版) ===\n")
    output.write(f"情报时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 实时盘面 ---\n")
    for k, v in base.items(): output.write(f"{k},{v}\n")
    
    output.write("\n--- 🤖 AI筹码底牌 (自动计算) ---\n")
    if chip:
        output.write(f"获利比例,{chip['profit_ratio']}%\n")
        output.write(f"平均成本,{chip['avg_cost']}\n")
        output.write(f"70%集中度,{chip['conc_70']}%\n")
    
    output.write("\n--- 🏢 深度财务 ---\n")
    for k, v in fin.items(): output.write(f"{k},{str(v).replace(',', ' ')}\n")
    
    output.write("\n--- 🕵️‍♂️ 风险确认 ---\n")
    output.write(f"状态,{user_risk['status']}\n备注,{user_risk['detail']}\n")
    
    output.write("\n--- 📈 历史K线 ---\n")
    df.to_csv(output, index=False)
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 4. 主界面逻辑
# ==========================================
if stock_code:
    with st.spinner('🚀 正在连接数据卫星...'):
        res = get_full_data(stock_code, lookback_days)
    
    if res and res[0] is not None:
        df, base, chip, fin = res
        
        # --- 标题栏 ---
        c1, c2 = st.columns([3, 1])
        with c1:
            st.title(f"{base['名称']} ({stock_code})")
            st.caption(f"数据源: {base['数据源']} | 行业: {fin.get('行业', '--')}")
        with c2:
            st.markdown(f"## {base['现价']}", unsafe_allow_html=True)

        # --- 核心仪表盘 ---
        m1, m2, m3, m4 = st.columns(4)
        if chip:
            m1.metric("获利比例 (AI算)", f"{chip['profit_ratio']}%")
            m2.metric("主力成本", f"{chip['avg_cost']}")
        else:
            m1.metric("获利比例", "--")
            m2.metric("主力成本", "--")
            
        m3.metric("市盈率", fin.get('市盈率', '--'))
        m4.metric("每股净资", fin.get('每股净资产', '--'))

        # --- ⬇️ 下载按钮 ---
        user_risk = {"status": risk_status, "detail": risk_detail}
        csv_data = create_csv(df, base, chip, fin, user_risk)
        now_str = datetime.datetime.now().strftime("%m%d_%H%M")
        
        st.download_button(
            label=f"📥 下载全息情报包 ({base['名称']})",
            data=csv_data,
            file_name=f"{base['名称']}_{stock_code}_{now_str}.csv",
            mime="text/csv",
            type="primary",
            use_container_width=True
        )

        # --- 可视化图表 ---
        tab1, tab2 = st.tabs(["K线趋势", "筹码分布"])
        
        with tab1:
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("价格", "成交量"))
            fig.add_trace(go.Candlestick(x=df['trade_date'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='K线'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['trade_date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['trade_date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
            fig.add_trace(go.Bar(x=df['trade_date'], y=df['volume'], name='成交量'), row=2, col=1)
            fig.update_layout(height=500, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)
            
        with tab2:
            if chip:
                chip_df = chip['chip_data']
                chip_df = chip_df[chip_df['Volume'] > 0.001]
                fig_chip = go.Figure()
                fig_chip.add_trace(go.Bar(
                    y=chip_df['Price'], x=chip_df['Volume'], 
                    orientation='h', 
                    marker_color=['red' if p < base['现价'] else 'green' for p in chip_df['Price']],
                    name='筹码'
                ))
                fig_chip.update_layout(title="筹码分布模拟图", height=500)
                fig_chip.add_hline(y=base['现价'], line_dash="dash", annotation_text="现价")
                st.plotly_chart(fig_chip, use_container_width=True)
            else:
                st.info("数据不足，无法生成筹码图")

    else:
        # 修复点：确保 res[1] 存在且可读
        error_msg = res[1] if res and len(res) > 1 else "未知网络错误"
        st.error(f"❌ 获取失败: {error_msg}")
