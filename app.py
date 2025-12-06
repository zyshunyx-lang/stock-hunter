import streamlit as st
import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io

# ==========================================
# 1. 页面配置与初始化
# ==========================================
st.set_page_config(page_title="猎人指挥中心 V3.2 (双引擎版)", layout="wide", page_icon="🦅")

# --- 侧边栏：目标锁定 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=800, value=500)

st.sidebar.markdown("---")
st.sidebar.header("🏔️ 筹码底牌录入 (必填)")
st.sidebar.info("机器抓不到筹码，请手动录入，这是风控灵魂！")

# 筹码数据录入
chip_profit = st.sidebar.number_input("获利比例 (%)", min_value=0.0, max_value=100.0, value=85.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)
chip_conc_70 = st.sidebar.number_input("70% 筹码集中度 (%)", value=15.0, help="越小越好，<20%为优")
chip_conc_90 = st.sidebar.number_input("90% 筹码集中度 (%)", value=30.0, help="看整体离散程度")

# ==========================================
# 2. 核心情报获取系统 (双引擎逻辑)
# ==========================================

@st.cache_data(ttl=3600)
def get_restricted_shares(code):
    """获取解禁数据 (排雷)"""
    try:
        df = ak.stock_restricted_release_queue_em() 
        df = df[df['code'] == code]
        if df.empty: return "无近期解禁记录"
        
        today = datetime.datetime.now()
        future_risk = []
        for index, row in df.iterrows():
            date_obj = pd.to_datetime(row['date'])
            if today < date_obj < today + datetime.timedelta(days=30):
                future_risk.append(f"⚠️ {row['date'].strftime('%Y-%m-%d')} 解禁 {row['ratio']}%")
        return " | ".join(future_risk) if future_risk else "未来30天无解禁 (安全)"
    except:
        return "解禁数据获取受限"

@st.cache_data(ttl=300)
def get_financial_info(code):
    """获取财务数据"""
    try:
        info = ak.stock_individual_info_em(symbol=code)
        data = {}
        for index, row in info.iterrows():
            data[row['item']] = row['value']
        return data
    except:
        return {}

def fetch_kline_eastmoney(code, start_str, end_str):
    """引擎A：东方财富接口 (速度快，数据全，但易被墙)"""
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        # 标准化列名
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume'})
        return df, "Eastmoney (东财)"
    except Exception as e:
        return None, str(e)

def fetch_kline_sina(code, start_str, end_str):
    """引擎B：新浪财经接口 (老牌接口，抗干扰能力强)"""
    try:
        # 新浪接口代码格式需要转换：sh600000 / sz000001
        sina_symbol = f"sh{code}" if code.startswith('6') else f"sz{code}"
        df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_str, end_date=end_str, adjust="qfq")
        # 新浪返回的列名通常是英文，需标准化
        # 假设返回：date, open, high, low, close, volume
        df = df.rename(columns={'date':'Date', 'open':'Open', 'close':'Close', 'high':'High', 'low':'Low', 'volume':'Volume'})
        return df, "Sina (新浪)"
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=60)
def get_all_data_robust(code, days):
    """双引擎调度指挥官"""
    try:
        # 1. 准备时间窗口
        end_str = datetime.datetime.now().strftime("%Y%m%d")
        start_date_obj = datetime.datetime.now() - datetime.timedelta(days=days*1.5)
        start_str = start_date_obj.strftime("%Y%m%d")
        
        # 2. 尝试启动引擎 A (东财)
        df, source = fetch_kline_eastmoney(code, start_str, end_str)
        
        # 3. 如果 A 失败，启动引擎 B (新浪)
        if df is None or df.empty:
            df, source = fetch_kline_sina(code, start_str, end_str)
            if df is None or df.empty:
                return None, "所有数据源均连接失败，请检查代码或稍后重试。"

        # 4. 数据清洗与指标计算 (统一处理)
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

        # 5. 获取实时行情与基本面 (尽量获取，失败则给空值)
        try:
            spot = ak.stock_zh_a_spot_em()
            row = spot[spot['代码'] == code]
            if not row.empty:
                name = row['名称'].values[0]
                price = row['最新价'].values[0]
                pct = f"{row['涨跌幅'].values[0]}%"
                turnover = f"{row['换手率'].values[0]}%"
                pe = row['市盈率-动态'].values[0]
                mkt_cap = f"{round(row['总市值'].values[0]/1e8, 2)}亿"
            else:
                raise ValueError("Spot data missing")
        except:
            # 如果实时接口也被墙，使用K线最后一天的收盘价兜底
            name = f"Code:{code}"
            price = df['Close'].iloc[-1]
            pct = "--"
            turnover = "--"
            pe = "--"
            mkt_cap = "--"

        # 6. 获取解禁与财务
        restricted_info = get_restricted_shares(code)
        fin_info = get_financial_info(code)
        
        intelligence = {
            "代码": code,
            "名称": name,
            "数据源": source,
            "行业": fin_info.get('行业', '未知'),
            "总市值": mkt_cap,
            "市盈率": pe,
            "换手率": turnover,
            "现价": price,
            "涨跌": pct,
            "风险_解禁": restricted_info
        }
        
        return df.tail(days), intelligence

    except Exception as e:
        return None, f"系统严重错误: {str(e)}"

# --- 生成 CSV ---
def create_csv_file(df, info, user_input):
    output = io.StringIO()
    output.write("=== 🦅 猎人作战情报包 V3.2 (双引擎版) ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    output.write("\n--- 🟢 基础情报 ---\n")
    for k, v in info.items():
        output.write(f"{k},{v}\n")
        
    output.write("\n--- 🏔️ 筹码底牌 (人工录入) ---\n")
    output.write(f"获利比例,{user_input['profit']}%\n")
    output.write(f"平均成本,{user_input['cost']}\n")
    output.write(f"70%集中度,{user_input['conc70']}%\n")
    output.write(f"90%集中度,{user_input['conc90']}%\n")
    
    output.write("\n--- 📈 历史量价 (数据流) ---\n")
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# ==========================================
# 3. 前端显示逻辑
# ==========================================
if stock_code:
    with st.spinner('🛰️ 正在调度双引擎卫星 (东财/新浪) ...'):
        df, info = get_all_data_robust(stock_code, lookback_days)
    
    if df is not None:
        # 抬头区
        col1, col2 = st.columns([3, 1])
        with col1:
            st.title(f"{info['名称']} ({info['代码']})")
            st.caption(f"数据来源: {info['数据源']} | 行业: {info['行业']}")
        with col2:
            # 颜色逻辑：涨红跌绿
            color = "red" 
            if "-" in str(info['涨跌']): color = "green"
            st.markdown(f"## <span style='color:{color}'>{info['现价']}</span>", unsafe_allow_html=True)
            st.markdown(f"**{info['涨跌']}**")
        
        # 核心指标区
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("换手率", info['换手率'])
        i2.metric("市盈率", info['市盈率'])
        i3.metric("总市值", info['总市值'])
        i4.metric("风险排查", "有雷" if "解禁" in info['风险_解禁'] and "无" not in info['风险_解禁'] else "安全")
        
        # 风险提示条
        if "无解禁" not in info['风险_解禁']:
            st.error(f"💣 **解禁警报**：{info['风险_解禁']}")
        
        # ----------------------------------------
        # 💾 下载区
        # ----------------------------------------
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{info['名称']}_{info['代码']}_{now_str}.csv"
        
        user_input = {
            "profit": chip_profit, "cost": avg_cost, 
            "conc70": chip_conc_70, "conc90": chip_conc_90
        }
        csv_data = create_csv_file(df, info, user_input)
        
        st.download_button(
            label=f"📥 一键下载情报包 (.csv)",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )
        # ----------------------------------------

        # 图表区
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], subplot_titles=("K线与均线", "成交量"))
        
        # K线
        fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K线'), row=1, col=1)
        # 均线
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], line=dict(color='purple', width=1.5), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA60'], line=dict(color='blue', width=1.5), name='MA60'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], line=dict(color='orange', width=1.5), name='MA250'), row=1, col=1)
        
        # 成交量
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=2, col=1)
        
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)
        
    else:
        st.error(f"❌ 侦察失败：{info}")
