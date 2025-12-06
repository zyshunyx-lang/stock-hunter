import streamlit as st
import pandas as pd
import numpy as np
import adata
import akshare as ak
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import pytz  # 用于时区修正
import io

# -----------------------------------------------------------------------------
# 0. 全局配置与辅助函数
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="猎人指挥中心 V8.2 (Cloud)",
    page_icon="🏹",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 样式美化
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    }
    .stRadio > label {font-weight: bold;}
</style>
""", unsafe_allow_html=True)

def get_beijing_time():
    """获取当前北京时间，用于修正云服务器的时区问题"""
    utc_now = datetime.datetime.now(pytz.utc)
    return utc_now.astimezone(pytz.timezone('Asia/Shanghai'))

def calculate_macd(df, short=12, long=26, mid=9):
    """计算 MACD 指标"""
    close = df['close']
    ema12 = close.ewm(span=short, adjust=False).mean()
    ema26 = close.ewm(span=long, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=mid, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif, dea, macd

# -----------------------------------------------------------------------------
# 1. 核心算法：筹码分布 (Chip Distribution)
# -----------------------------------------------------------------------------
def calc_chip_distribution(df, decimals=2):
    """
    计算筹码分布
    逻辑：每日新筹码 = 换手率 * 收盘价；历史筹码 = 历史筹码 * (1-换手率)
    """
    chip_dict = {} # {price_bin: weight}
    
    # 确保有换手率，没有则模拟
    if 'turnover_ratio' not in df.columns:
        df['turnover_ratio'] = 1.0 # 默认 1%
    else:
        df['turnover_ratio'] = df['turnover_ratio'].fillna(1.0)

    # 遍历历史数据计算筹码沉淀
    for index, row in df.iterrows():
        price = round(row['close'], decimals)
        turnover = row['turnover_ratio'] / 100 
        
        # 1. 历史筹码衰减
        for p in list(chip_dict.keys()):
            chip_dict[p] = chip_dict[p] * (1 - turnover)
        
        # 2. 新增当日筹码
        if price in chip_dict:
            chip_dict[price] += turnover
        else:
            chip_dict[price] = turnover

    # 转换为 DataFrame 用于分析
    chip_df = pd.DataFrame(list(chip_dict.items()), columns=['price', 'volume'])
    chip_df = chip_df.sort_values('price')
    
    # 归一化
    total_vol = chip_df['volume'].sum()
    if total_vol > 0:
        chip_df['volume'] = chip_df['volume'] / total_vol
    
    # 计算累积分布用于计算集中度
    chip_df['cumsum_vol'] = chip_df['volume'].cumsum()
    
    return chip_df

def get_chip_metrics(chip_df, current_price):
    """计算筹码核心指标"""
    if chip_df.empty:
        return 0, 0, 0, 0
    
    # 获利比例 (收盘价以下的筹码占比)
    profit_df = chip_df[chip_df['price'] <= current_price]
    profit_ratio = profit_df['volume'].sum() * 100
    
    # 平均成本
    avg_cost = (chip_df['price'] * chip_df['volume']).sum()
    
    # 筹码集中度计算 (90%筹码分布的价格区间)
    try:
        p05 = chip_df[chip_df['cumsum_vol'] >= 0.05].iloc[0]['price']
        p95 = chip_df[chip_df['cumsum_vol'] >= 0.95].iloc[0]['price']
        concentration_90 = (p95 - p05) / (p05 + p95) * 2 * 100
    except:
        concentration_90 = 0
        
    return profit_ratio, avg_cost, concentration_90, chip_df

# -----------------------------------------------------------------------------
# 2. 数据获取模块 (Data Fetching)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=600) # 缓存 10 分钟，减少云端请求压力
def get_full_data(code, days):
    """获取全息数据：K线、实时、财务、筹码"""
    data_bundle = {}
    
    # ---------------- Step 1: 历史 K 线 (Adata) ----------------
    try:
        df = adata.stock.market.get_market(stock_code=code, k_type=1)
        
        if df is None or df.empty:
            return None, "Adata 未返回 K 线数据，可能是代码错误或接口限流。"
        
        # 数据清洗
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.sort_values('trade_date').reset_index(drop=True)
        
        if len(df) > days:
            df = df.iloc[-days:].reset_index(drop=True)
            
        cols = ['open', 'high', 'low', 'close', 'volume', 'turnover_ratio']
        for c in cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        
        # 计算技术指标
        for ma in [5, 20, 60, 250]:
            df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
        df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
        
        data_bundle['history'] = df

    except Exception as e:
        return None, f"获取历史 K 线失败: {str(e)}"

    # ---------------- Step 2: 筹码计算 ----------------
    try:
        chip_raw_df = calc_chip_distribution(df)
        current_price = df.iloc[-1]['close']
        profit_ratio, avg_cost, concentration, chip_final_df = get_chip_metrics(chip_raw_df, current_price)
        
        data_bundle['chip_metrics'] = {
            'profit_ratio': profit_ratio,
            'avg_cost': avg_cost,
            'concentration_90': concentration
        }
        data_bundle['chip_data'] = chip_final_df
    except Exception as e:
        return None, f"筹码计算失败: {str(e)}"

    # ---------------- Step 3: 实时行情 ----------------
    try:
        real_df = adata.stock.market.list_market_current(stock_code=code)
        if real_df is not None and not real_df.empty:
            data_bundle['realtime'] = real_df.iloc[0].to_dict()
        else:
            last_row = df.iloc[-1]
            data_bundle['realtime'] = {
                'short_name': code, 
                'price': last_row['close'], 
                'change_pct': 0.0
            }
    except Exception as e:
         data_bundle['realtime'] = {'error': str(e)}

    # ---------------- Step 4: 深度财务 ----------------
    try:
        info_df = ak.stock_individual_info_em(symbol=code)
        info_dict = dict(zip(info_df['item'], info_df['value']))
        data_bundle['financial'] = info_dict
    except Exception as e:
        data_bundle['financial'] = {}

    return data_bundle, None

# -----------------------------------------------------------------------------
# 3. 主界面逻辑
# -----------------------------------------------------------------------------

# 侧边栏
st.sidebar.title("🏹 猎人指挥中心 V8.2")
st.sidebar.caption("云端部署版 | 北京时间")
st.sidebar.markdown("---")
input_code = st.sidebar.text_input("股票代码 (6位)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", 200, 1000, 500)

st.sidebar.markdown("### 🛡️ 风控确认")
risk_check = st.sidebar.radio("未来30天解禁/减持风险", ["✅ 安全", "⚠️ 有风险/不确定"], index=0)
risk_notes = st.sidebar.text_area("情报备注", placeholder="在此记录股东动态或利好利空...")

# 运行按钮
if st.sidebar.button("🚀 启动分析引擎", type="primary"):
    with st.spinner('正在链接云端数据源，计算筹码分布...'):
        data, err = get_full_data(input_code, lookback_days)

    if err:
        st.error(f"系统故障: {err}")
    else:
        # 提取数据
        hist_df = data['history']
        rt_data = data['realtime']
        fin_data = data['financial']
        chip_metrics = data['chip_metrics']
        chip_dist_df = data['chip_data']

        # ---------------- 标题栏 ----------------
        name = rt_data.get('short_name', fin_data.get('股票简称', input_code))
        price = rt_data.get('price', hist_df.iloc[-1]['close'])
        
        try:
            pct_change = float(rt_data.get('change_pct', 0))
        except:
            pct_change = 0
            
        color_change = "red" if pct_change > 0 else "green"
        
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        with c1:
            st.metric("股票名称", f"{name} ({input_code})")
        with c2:
            st.markdown(f"#### 当前价格: <span style='color:{color_change}'>{price}</span>", unsafe_allow_html=True)
        with c3:
            st.markdown(f"#### 涨跌幅: <span style='color:{color_change}'>{pct_change}%</span>", unsafe_allow_html=True)
        with c4:
            industry = fin_data.get('行业', '未知')
            st.metric("所属行业", industry)

        st.markdown("---")

        # ---------------- 核心仪表盘 ----------------
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("💰 获利盘比例", f"{chip_metrics['profit_ratio']:.2f}%")
        with m2:
            st.metric("🎯 主力平均成本", f"{chip_metrics['avg_cost']:.2f}")
        with m3:
            pe = fin_data.get('市盈率(动)', fin_data.get('市盈率(TTM)', '-'))
            st.metric("市盈率 (PE)", pe)
        with m4:
            pb = fin_data.get('每股净资产', '-')
            st.metric("每股净资产", pb)

        # ---------------- 下载区域 (已修正时区) ----------------
        export_df = hist_df.copy()
        
        # 使用北京时间函数
        bj_time = get_beijing_time()
        export_df['export_time'] = bj_time
        
        export_df['risk_status'] = risk_check
        export_df['risk_notes'] = risk_notes
        export_df['chip_profit_ratio'] = chip_metrics['profit_ratio']
        
        for k, v in fin_data.items():
            export_df.loc[0, f"fin_{k}"] = v

        csv = export_df.to_csv(index=False).encode('utf-8-sig')
        
        st.download_button(
            label="📥 下载全息情报包 (.csv)"，
            data=csv,
            file_name=f"Hunter_Report_{input_code}_{bj_time.strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv"，
        )

        # ---------------- 图表区域 ----------------
        tab1, tab2 = st.tabs(["📊 K线技术分析"， "🧩 筹码分布模拟"])

        with tab1:
            # K线图配置
            fig_k = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                                  vertical_spacing=0.03, row_heights=[0.7, 0.3])
            
            fig_k.add_trace(go.Candlestick(
                x=hist_df['trade_date'],
                open=hist_df['open'], high=hist_df['high'],
                low=hist_df['low'], close=hist_df['close'],
                name='K线'
            ), row=1, col=1)
            
            colors = {'MA5': 'orange', 'MA20': 'purple', 'MA60': 'blue', 'MA250': 'black'}
            for ma_name, color in colors.items():
                if ma_name in hist_df.columns:
                    fig_k.add_trace(go.Scatter(
                        x=hist_df['trade_date'], y=hist_df[ma_name],
                        mode='lines', name=ma_name, line=dict(color=color, width=1)
                    ), row=1, col=1)
            
            vol_colors = ['red' if r['close'] >= r['open'] else 'green' for i, r in hist_df.iterrows()]
            fig_k.add_trace(go.Bar(
                x=hist_df['trade_date'], y=hist_df['volume'],
                name='成交量', marker_color=vol_colors
            ), row=2, col=1)

            fig_k.update_layout(xaxis_rangeslider_visible=False, height=600, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig_k, use_container_width=True)

        with tab2:
            # 筹码图配置
            current_p = float(price)
            chip_profit = chip_dist_df[chip_dist_df['price'] <= current_p]
            chip_loss = chip_dist_df[chip_dist_df['price'] > current_p]
            
            fig_chip = go.Figure()
            
            fig_chip.add_trace(go.Bar(
                y=chip_profit['price'], x=chip_profit['volume'],
                orientation='h', name='获利盘', marker_color='red', opacity=0.6
            ))
            
            fig_chip.add_trace(go.Bar(
                y=chip_loss['price'], x=chip_loss['volume'],
                orientation='h', name='套牢盘', marker_color='green', opacity=0.6
            ))
            
            fig_chip.add_hline(y=current_p, line_dash="dash", line_color="black", annotation_text="当前价")
            
            fig_chip.update_layout(
                title="筹码成本分布 (Chip Distribution)"，
                xaxis_title="筹码量 (相对比例)",
                yaxis_title="价格"，
                height=600,
                bargap=0.0, 
                showlegend=True
            )
            st.plotly_chart(fig_chip, use_container_width=True)
            
            st.info(f"""
            **筹码解读**:
            - 90% 筹码集中度: **{chip_metrics['concentration_90']:.2f}%**
            - 红色区域代表成本低于当前价的获利筹码。
            - 绿色区域代表成本高于当前价的套牢筹码。
            """)

else:
    st.info("👈 请在左侧输入股票代码并点击【启动分析引擎】")
