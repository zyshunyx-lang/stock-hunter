import streamlit as st
import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import io

# --- 页面配置 ---
st.set_page_config(page_title="猎人指挥中心 V3.0", layout="wide", page_icon="🦅")

# --- 侧边栏：目标锁定 ---
st.sidebar.header("🎯 目标锁定")
stock_code = st.sidebar.text_input("输入代码 (如 603909)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", min_value=120, max_value=800, value=500)

st.sidebar.markdown("---")
st.sidebar.header("🏔️ 唯一需要人工的情报")
st.sidebar.info("机器抓不到【筹码分布】，请看着手机填一下，这是风控的核心！")
chip_profit = st.sidebar.number_input("获利比例 (%)", min_value=0.0, max_value=100.0, value=85.0)
chip_concentration = st.sidebar.number_input("70%筹码集中度 (%)", value=15.0)
avg_cost = st.sidebar.number_input("平均成本 (元)", value=0.0)

# --- 核心工具函数 ---

@st.cache_data(ttl=3600) # 缓存1小时，解禁数据不常变
def get_restricted_shares(code):
    """获取解禁数据，判断未来风险"""
    try:
        # 获取未来解禁表 (取最近的记录)
        df = ak.stock_restricted_release_queue_em() 
        # 筛选该股票
        df = df[df['code'] == code]
        if df.empty:
            return "无近期解禁记录"
        
        # 检查未来30天
        today = datetime.datetime.now()
        future_risk = []
        for index, row in df.iterrows():
            date_obj = pd.to_datetime(row['date'])
            if today < date_obj < today + datetime.timedelta(days=30):
                future_risk.append(f"⚠️ {row['date'].strftime('%Y-%m-%d')} 解禁 {row['ratio']}%")
        
        if future_risk:
            return " | ".join(future_risk)
        else:
            return "未来30天无解禁 (安全)"
    except:
        return "解禁数据获取失败"

@st.cache_data(ttl=300)
def get_main_index_data(stock_code, start_date, end_date):
    """根据个股代码，自动匹配大盘指数数据"""
    try:
        # 简单逻辑：6开头对标上证(sh000001)，其他对标深证(sz399001)
        index_code = "sh000001" if stock_code.startswith("6") else "sz399001"
        index_df = ak.stock_zh_index_daily(symbol=index_code)
        
        # 过滤时间
        mask = (index_df['date'] >= pd.to_datetime(start_date)) & (index_df['date'] <= pd.to_datetime(end_date))
        index_df = index_df.loc[mask]
        index_df = index_df.rename(columns={'date':'Date', 'close':'Index_Close'})
        index_df = index_df[['Date', 'Index_Close']]
        index_df['Date'] = pd.to_datetime(index_df['Date'])
        return index_df, index_code
    except:
        return pd.DataFrame(), "Unknown"

@st.cache_data(ttl=300)
def get_financial_info(code):
    """获取个股核心财务指标"""
    try:
        # 使用 akshare 个股信息接口
        info = ak.stock_individual_info_em(symbol=code)
        # 整理为字典
        data = {}
        for index, row in info.iterrows():
            data[row['item']] = row['value']
        return data
    except:
        return {}

@st.cache_data(ttl=60)
def get_all_data(code, days):
    try:
        # 1. 实时行情
        spot = ak.stock_zh_a_spot_em()
        row = spot[spot['代码'] == code]
        if row.empty: return None, "代码错误"
        
        # 2. 历史K线
        end_str = datetime.datetime.now().strftime("%Y%m%d")
        start_date_obj = datetime.datetime.now() - datetime.timedelta(days=days*1.5)
        start_str = start_date_obj.strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
        df = df.rename(columns={'日期':'Date', '开盘':'Open', '收盘':'Close', '最高':'High', '最低':'Low', '成交量':'Volume'})
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 3. 获取同期大盘数据
        index_df, index_name = get_main_index_data(code, start_date_obj.strftime("%Y-%m-%d"), datetime.datetime.now().strftime("%Y-%m-%d"))
        
        # 4. 合并大盘数据 (计算相对强度)
        if not index_df.empty:
            df = pd.merge(df, index_df, on='Date', how='left')
            # 归一化对比 (以第一天为基准100)
            df['Stock_Norm'] = df['Close'] / df['Close'].iloc[0] * 100
            df['Index_Norm'] = df['Index_Close'] / df['Index_Close'].iloc[0] * 100
            df['RS_Rating'] = df['Stock_Norm'] - df['Index_Norm'] # 相对强度 RS

        # 5. 计算均线 & MACD
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA120'] = df['Close'].rolling(120).mean()
        
        exp12 = df['Close'].ewm(span=12, adjust=False).mean()
        exp26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp12 - exp26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])

        # 6. 获取解禁排雷 & 财务 & 行业
        restricted_info = get_restricted_shares(code)
        fin_info = get_financial_info(code)
        
        # 整合情报字典
        intelligence = {
            "代码": code,
            "名称": row['名称'].values[0],
            "行业": fin_info.get('行业', '未知'),
            "总市值": f"{round(row['总市值'].values[0]/1e8, 2)}亿",
            "市盈率": row['市盈率-动态'].values[0],
            "换手率": f"{row['换手率'].values[0]}%",
            "现价": row['最新价'].values[0],
            "涨跌": f"{row['涨跌幅'].values[0]}%",
            "风险_解禁": restricted_info,
            "对标指数": index_name
        }
        
        return df.tail(days), intelligence

    except Exception as e:
        return None, str(e)

# --- 生成 CSV ---
def create_csv_file(df, info, user_input):
    output = io.StringIO()
    
    # 1. 战略情报头
    output.write("=== 🦅 猎人作战情报包 V3.0 ===\n")
    output.write(f"生成时间,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    output.write("\n--- 🟢 基础情报 ---\n")
    for k, v in info.items():
        output.write(f"{k},{v}\n")
        
    output.write("\n--- 🏔️ 筹码底牌 (人工录入) ---\n")
    output.write(f"获利比例,{user_input['profit']}%\n")
    output.write(f"平均成本,{user_input['cost']}\n")
    output.write(f"70%集中度,{user_input['conc']}%\n")
    
    output.write("\n--- 📈 每日行情与大盘对比 (数据流) ---\n")
    # 导出
    df.to_csv(output, index=False)
    
    return output.getvalue().encode('utf-8-sig')

# --- 主界面 ---
if stock_code:
    with st.spinner('🛰️ 正在调用卫星扫描全市场数据...'):
        df, info = get_all_data(stock_code, lookback_days)
    
    if df is not None:
        # 标题区
        c1, c2 = st.columns([3, 1])
        c1.title(f"{info['名称']} ({info['代码']})")
        color = "red" if "-" not in str(info['涨跌']) else "green"
        c2.markdown(f"## <span style='color:{color}'>{info['现价']}</span>", unsafe_allow_html=True)
        
        # 核心情报展示
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("行业", info['行业'])
        i2.metric("换手率", info['换手率'])
        i3.metric("总市值", info['总市值'])
        i4.metric("市盈率", info['市盈率'])
        
        # ⚠️ 自动排雷结果展示
        if "无解禁" not in info['风险_解禁']:
            st.error(f"💣 **解禁警报**：{info['风险_解禁']}")
        else:
            st.success(f"🛡️ **解禁排查**：{info['风险_解禁']}")

        # ------------------------------------------------
        # 💾 一键下载区 (自动化文件名)
        # ------------------------------------------------
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M")
        file_name = f"{info['名称']}_{info['代码']}_{now_str}.csv"
        
        user_input = {"profit": chip_profit, "conc": chip_concentration, "cost": avg_cost}
        csv_data = create_csv_file(df, info, user_input)
        
        st.download_button(
            label=f"📥 下载情报包：{file_name}",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            type="primary",
            use_container_width=True
        )
        # ------------------------------------------------

        # 📊 绘图：K线 + 大盘对比
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, 
                            row_heights=[0.6, 0.2, 0.2],
                            subplot_titles=("K线与均线", "相对强度 (个股 vs 大盘)", "成交量"))
        
        # 1. K线
        fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='个股K线'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], line=dict(color='purple', width=1), name='MA20'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA60'], line=dict(color='blue', width=1), name='MA60'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], line=dict(color='orange', width=1), name='MA250(年线)'), row=1, col=1)
        
        # 2. 相对强度 (RS Rating)
        # 如果 RS > 0，说明跑赢大盘；RS < 0，说明跑输
        colors_rs = ['red' if val >= 0 else 'green' for val in df['RS_Rating']]
        fig.add_trace(go.Bar(x=df['Date'], y=df['RS_Rating'], marker_color=colors_rs, name='相对强度(跑赢大盘%)'), row=2, col=1)
        
        # 3. 成交量
        fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量'), row=3, col=1)
        
        fig.update_layout(height=800, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)
        
    else:
        st.error(info)