import streamlit as st
import pandas as pd
import numpy as np
import akshare as ak  # 全面替换为 akshare
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import pytz
import io

# -----------------------------------------------------------------------------
# 0. 全局配置与辅助函数
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="猎人指挥中心 V8.3 (修复版)",
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
    """获取当前北京时间"""
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
# 1. 核心算法：筹码分布
# -----------------------------------------------------------------------------
def calc_chip_distribution(df, decimals=2):
    chip_dict = {} 
    
    # Akshare 的换手率通常是数值 (例如 2.5 代表 2.5%)
    # 也可以检查数据范围，如果全是 0-1 之间需 *100，如果是 0-100 则直接用
    if 'turnover_ratio' not in df.columns:
        df['turnover_ratio'] = 1.0 
    else:
        df['turnover_ratio'] = df['turnover_ratio'].fillna(1.0)

    for index, row in df.iterrows():
        price = round(row['close'], decimals)
        # 假设换手率是百分数 (e.g., 1.5)，算法需要小数 (0.015)
        turnover = row['turnover_ratio'] / 100 
        
        for p in list(chip_dict.keys()):
            chip_dict[p] = chip_dict[p] * (1 - turnover)
        
        if price in chip_dict:
            chip_dict[price] += turnover
        else:
            chip_dict[price] = turnover

    chip_df = pd.DataFrame(list(chip_dict.items()), columns=['price', 'volume'])
    chip_df = chip_df.sort_values('price')
    
    total_vol = chip_df['volume'].sum()
    if total_vol > 0:
        chip_df['volume'] = chip_df['volume'] / total_vol
    
    chip_df['cumsum_vol'] = chip_df['volume'].cumsum()
    
    return chip_df

def get_chip_metrics(chip_df, current_price):
    if chip_df.empty:
        return 0, 0, 0, 0
    
    profit_df = chip_df[chip_df['price'] <= current_price]
    profit_ratio = profit_df['volume'].sum() * 100
    
    avg_cost = (chip_df['price'] * chip_df['volume']).sum()
    
    try:
        p05 = chip_df[chip_df['cumsum_vol'] >= 0.05].iloc[0]['price']
        p95 = chip_df[chip_df['cumsum_vol'] >= 0.95].iloc[0]['price']
        concentration_90 = (p95 - p05) / (p05 + p95) * 2 * 100
    except:
        concentration_90 = 0
        
    return profit_ratio, avg_cost, concentration_90, chip_df

# -----------------------------------------------------------------------------
# 2. 数据获取模块 (Akshare 重构版)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=600)
def get_full_data(code, days):
    data_bundle = {}
    
    # ---------------- Step 1: 历史 K 线 (Akshare) ----------------
    try:
        # 使用 akshare 获取历史行情 (日线, 前复权)
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        
        if df is None or df.empty:
            return None, "Akshare 未返回数据，请检查股票代码是否正确 (如: 603909)。"
        
        # 重命名列以匹配算法
        # akshare 列名: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        rename_map = {
            '日期': 'trade_date',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume',
            '换手率': 'turnover_ratio'
        }
        df = df.rename(columns=rename_map)
        
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        # 截取时间
        if len(df) > days:
            df = df.iloc[-days:].reset_index(drop=True)
            
        # 确保数值类型
        cols = ['open', 'high', 'low', 'close', 'volume', 'turnover_ratio']
        for c in cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        
        # 计算均线
        for ma in [5, 20, 60, 250]:
            df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
        
        # 计算 MACD
        df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
        
        data_bundle['history'] = df

    except Exception as e:
        return None, f"获取历史 K 线失败 (Akshare): {str(e)}"

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

    # ---------------- Step 3: 深度财务 & 基础信息 ----------------
    try:
        # 个股信息 (包含 总市值, 行业, 上市时间 等)
        info_df = ak.stock_individual_info_em(symbol=code)
        info_dict = dict(zip(info_df['item'], info_df['value']))
        data_bundle['financial'] = info_dict
    except Exception as e:
        data_bundle['financial'] = {}

    # ---------------- Step 4: 实时行情 (模拟/获取) ----------------
    # 获取实时价格比较耗时(需拉取全市场)，这里使用策略：
    # 如果今天是交易日且在盘中，尝试获取分钟级数据最后一行作为实时数据
    # 否则使用日线最后一行
    try:
        realtime_data = {
            'short_name': data_bundle['financial'].get('股票简称', code),
            'price': df.iloc[-1]['close'],
            'change_pct': df.iloc[-1].get('涨跌幅', 0.0)
        }
        
        # 尝试获取分钟数据以获得最新价格 (仅取最近1分钟)
        try:
            min_df = ak.stock_zh_a_hist_min_em(symbol=code, period='1', adjust='')
            if not min_df.empty:
                latest = min_df.iloc[-1]
                # 分钟数据列名: 时间, 开盘, 收盘, 最高, 最低...
                realtime_data['price'] = latest['收盘']
                # 分钟数据没有涨跌幅，仍沿用日线或需额外计算，这里简化处理
        except:
            pass # 如果获取分钟失败，就用日线收盘价

        data_bundle['realtime'] = realtime_data
        
    except Exception as e:
         data_bundle['realtime'] = {'error': str(e)}

    return data_bundle, None

# -----------------------------------------------------------------------------
# 3. 主界面逻辑
# -----------------------------------------------------------------------------
st.sidebar.title("🏹 猎人指挥中心 V8.3")
st.sidebar.caption("Akshare 稳定内核版")
st.sidebar.markdown("---")
input_code = st.sidebar.text_input("股票代码 (6位)", value="603909")
lookback_days = st.sidebar.slider("K线回看天数", 200, 1000, 500)

st.sidebar.markdown("### 🛡️ 风控确认")
risk_check = st.sidebar.radio("未来30天解禁/减持风险", ["✅ 安全", "⚠️ 有风险/不确定"], index=0)
risk_notes = st.sidebar.text_area("情报备注", placeholder="在此记录股东动态...")

if st.sidebar.button("🚀 启动分析引擎", type="primary"):
    with st.spinner('正在链接 Akshare 数据源...'):
        data, err = get_full_data(input_code, lookback_days)

    if err:
        st.error(f"❌ 错误: {err}")
        st.warning("提示：请检查股票代码是否正确，或稍后重试。")
    else:
        hist_df = data['history']
        rt_data = data['realtime']
        fin_data = data['financial']
        chip_metrics = data['chip_metrics']
        chip_dist_df = data['chip_data']

        # ---------------- 标题栏 ----------------
        name = rt_data.get('short_name', input_code)
        price = rt_data.get('price', '-')
        
        # 涨跌幅处理
        try:
            # Akshare 日线数据中有 '涨跌幅' 列
            pct_change = hist_df.iloc[-1].get('涨跌幅', 0)
        except:
            pct_change = 0
            
        color_change = "red" if float(pct_change) > 0 else "green"
        
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

        # ---------------- 仪表盘 ----------------
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("💰 获利盘比例", f"{chip_metrics['profit_ratio']:.2f}%")
        with m2:
            st.metric("🎯 主力平均成本", f"{chip_metrics['avg_cost']:.2f}")
        with m3:
            pe = fin_data.get('市盈率(动)', fin_data.get('市盈率(TTM)', '-'))
            st.metric("市盈率 (PE)", pe)
        with m4:
            val = fin_data.get('总市值', '-')
            # 格式化市值
            if isinstance(val, (int, float)):
                val_str = f"{val/100000000:.2f} 亿"
            else:
                val_str = val
            st.metric("总市值", val_str)

        # ---------------- 下载 ----------------
        export_df = hist_df.copy()
        bj_time = get_beijing_time()
        export_df['export_time'] = bj_time
        export_df['risk_status'] = risk_check
        export_df['risk_notes'] = risk_notes
        export_df['chip_profit_ratio'] = chip_metrics['profit_ratio']
        
        for k, v in fin_data.items():
            export_df.loc[0， f"fin_{k}"] = v

        csv = export_df.to_csv(index=False)。encode('utf-8-sig')
        
        st.download_button(
            label="📥 下载全息情报包 (.csv)"，
            data=csv,
            file_name=f"Hunter_{input_code}_{bj_time.strftime('%Y%m%d')}.csv",
            mime="text/csv"，
        )

        # ---------------- 图表 ----------------
        tab1, tab2 = st.tabs(["📊 K线技术分析", "🧩 筹码分布模拟"])

        with tab1:
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
            
            # 简单的涨红跌绿
            vol_colors = ['red' if r['close'] >= r['open'] else 'green' for i, r in hist_df.iterrows()]
            fig_k.add_trace(go.Bar(
                x=hist_df['trade_date'], y=hist_df['volume']，
                name='成交量', marker_color=vol_colors
            ), row=2, col=1)

            fig_k.update_layout(xaxis_rangeslider_visible=False, height=600, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig_k, use_container_width=True)

        with tab2:
            try:
                current_p = float(price)
            except:
                current_p = hist_df.iloc[-1]['close']

            chip_profit = chip_dist_df[chip_dist_df['price'] <= current_p]
            chip_loss = chip_dist_df[chip_dist_df['price'] > current_p]
            
            fig_chip = go.Figure()
            
            fig_chip.add_trace(go.Bar(
                y=chip_profit['price'], x=chip_profit['volume']，
                orientation='h', name='获利盘', marker_color='red', opacity=0.6
            ))
            
            fig_chip.add_trace(go.Bar(
                y=chip_loss['price'], x=chip_loss['volume']，
                orientation='h', name='套牢盘', marker_color='green', opacity=0.6
            ))
            
            fig_chip.add_hline(y=current_p, line_dash="dash", line_color="black", annotation_text="当前价")
            
            fig_chip.update_layout(
                title=f"筹码成本分布 (Chip Distribution) - 90%集中度: {chip_metrics['concentration_90']:.2f}%",
                xaxis_title="筹码量 (相对比例)"，
                yaxis_title="价格"，
                height=600，
                bargap=0.0, 
                showlegend=True
            )
            st.plotly_chart(fig_chip, use_container_width=True)

else:
    st.info("👈 请在左侧输入股票代码并点击【启动分析引擎】")
