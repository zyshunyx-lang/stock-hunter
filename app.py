import streamlit as st
import pandas as pd
import akshare as ak
import datetime
import pytz
import time
import random
import numpy as np

# ----------------------------------------------------------------------------- 
# 0. 全局配置
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter Data Fetcher (Final)",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 核心工具函数
# -----------------------------------------------------------------------------
def get_symbol_prefix(code):
    """自动补充代码前缀"""
    if not code or not isinstance(code, str): return code
    code = str(code).strip()
    if code.startswith('6'): return f"sh{code}"
    if code.startswith('0') or code.startswith('3'): return f"sz{code}"
    if code.startswith('8') or code.startswith('4'): return f"bj{code}"
    return code

def add_technical_indicators(df):
    """添加技术指标 (MACD, KDJ, RSI, BOLL, MA, VWAP)"""
    try:
        close = df['close']
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['DIF'] = ema12 - ema26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = (df['DIF'] - df['DEA']) * 2
        # MA
        for w in [5, 10, 20, 60]: df[f'MA{w}'] = close.rolling(window=w).mean()
        # RSI
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        for p in [6, 12, 24]:
            ma_up = up.ewm(com=p-1, adjust=False).mean()
            ma_down = down.ewm(com=p-1, adjust=False).mean()
            df[f'RSI_{p}'] = ma_up / (ma_up + ma_down) * 100
        # BOLL
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        df['BOLL_UP'] = mid + 2*std
        df['BOLL_LO'] = mid - 2*std
        # KDJ
        low_min = df['low'].rolling(9).min()
        high_max = df['high'].rolling(9).max()
        rsv = (close - low_min) / (high_max - low_min) * 100
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']
        # VWAP (日内近似)
        if 'amount' in df.columns:
            df['VWAP'] = df.apply(lambda x: x['amount']/x['volume'] if x['volume']>0 else x['close'], axis=1)
    except: pass
    return df

def clean_data(df):
    """清洗列名"""
    col_map = {
        '日期':'trade_date', 'date':'trade_date', '开盘':'open', 'open':'open',
        '收盘':'close', 'close':'close', '最高':'high', 'high':'high', '最低':'low', 'low':'low',
        '成交量':'volume', 'volume':'volume', '成交额':'amount', 'amount':'amount',
        '换手率':'turnover', '涨跌幅':'pct_chg'
    }
    df = df.rename(columns=col_map)
    if 'trade_date' in df:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    return df

# ----------------------------------------------------------------------------- 
# 2. 稳健的名称获取 (三级火箭策略)
# -----------------------------------------------------------------------------
def get_stock_name_robust(code, user_manual_name=None):
    """
    1. 如果用户填了名字，直接用用户的。
    2. 如果没填，尝试联网查。
    3. 如果联网失败，直接返回代码，不报错。
    """
    code = str(code).strip()
    
    # 策略 1: 用户手动覆盖 (最高优先级)
    if user_manual_name and user_manual_name.strip():
        return user_manual_name.strip(), "手动输入"
        
    # 策略 2: 尝试联网单点查询 (akshare个股资料)
    try:
        # 这个接口通常比全市场列表要快且稳定
        df = ak.stock_individual_info_em(symbol=code)
        info = dict(zip(df['item'], df['value']))
        name = info.get('股票简称', None)
        if name:
            return name, "自动识别"
    except:
        pass
        
    # 策略 3: 彻底失败，返回代码作为名字 (保底)
    return f"Stock_{code}", "未知(已强制执行)"

# ----------------------------------------------------------------------------- 
# 3. 数据获取引擎 (含重试)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def fetch_data_engine(code, days):
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    logs = []
    df = None
    
    # 尝试东财 (数据最全)
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s_str, end_date=e_str, adjust="qfq")
        if df is not None and not df.empty:
            df = clean_data(df)
            logs.append("✅ 来源: 东方财富")
    except Exception as e:
        logs.append(f"❌ 东财接口异常: {e}")
        
    # 尝试新浪 (备用)
    if df is None:
        try:
            time.sleep(0.5)
            sym = get_symbol_prefix(code)
            df = ak.stock_zh_a_daily(symbol=sym, start_date=s_str, end_date=e_str, adjust="qfq")
            if df is not None and not df.empty:
                df = clean_data(df)
                logs.append("✅ 来源: 新浪财经")
        except Exception as e:
            logs.append(f"❌ 新浪接口异常: {e}")

    if df is None:
        return None, "所有接口均无数据，请检查代码是否正确或退市。", logs
        
    # 计算指标
    df = add_technical_indicators(df)
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. 用户界面 (手动兜底版)
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter Pro (终极容错版)")
st.sidebar.caption("解决“无法识别”问题的最终方案")
st.sidebar.markdown("---")

# --- 输入区 ---
st.sidebar.markdown("### 1. 股票设定")
col1, col2 = st.sidebar.columns([1, 1.5])
input_code = col1.text_input("代码", value="002860")
# 关键修改：允许用户直接输入名字，绕过API
input_name = col2.text_input("名称 (可选)", value="", placeholder="若识别失败请填此")

days = st.sidebar.slider("回溯天数", 30, 2000, 365)

# --- 自动识别尝试 ---
# 当代码输入完，界面刷新时，尝试自动给个提示，但不阻塞
auto_name = "..."
if len(input_code) == 6:
    if not input_name:
        st.sidebar.caption(f"正在尝试后台识别 {input_code} ...")
else:
    st.sidebar.warning("请输入 6 位股票代码")

st.sidebar.markdown("---")

# --- 执行按钮 ---
if st.sidebar.button("🚀 强制获取数据", type="primary"):
    if len(input_code) != 6:
        st.error("代码格式错误，必须是6位数字！")
    else:
        # 1. 确定名字 (绝不报错)
        final_name, name_source = get_stock_name_robust(input_code, input_name)
        
        # 2. 获取数据
        with st.spinner(f"正在为 【{final_name}】 ({input_code}) 拉取数据..."):
            df, err, logs = fetch_data_engine(input_code, days)
            
        if err:
            st.error(err)
            with st.expander("错误日志"):
                st.write(logs)
        else:
            # 3. 成功展示
            st.success(f"获取成功！股票: {final_name} | 来源: {name_source}")
            
            # 补全信息
            df['code'] = input_code
            df['name'] = final_name
            
            # 顶部数据展示
            last = df.iloc[-1]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("名称", final_name)
            c2.metric("最新价", f"{last['close']:.2f}")
            pct = last.get('pct_chg', 0)
            color = "red" if pct > 0 else "green"
            c3.markdown(f"#### 涨跌: <span style='color:{color}'>{pct:.2f}%</span>", unsafe_allow_html=True)
            c4.metric("记录数", len(df))
            
            st.markdown("---")
            
            # 4. 下载 (文件名修复)
            # 过滤非法字符，确保文件名合法
            safe_name = str(final_name).replace("*", "").replace(":", "").replace("?", "").replace("/", "")
            file_time = datetime.datetime.now().strftime("%Y%m%d")
            file_name = f"【{safe_name}_{file_time}】.csv"
            
            csv_data = df.to_csv(index=False).encode('utf-8-sig')
            
            st.download_button(
                label=f"📥 下载 CSV 文件: {file_name}",
                data=csv_data,
                file_name=file_name,
                mime="text/csv",
                type="primary"
            )
            
            st.caption("✅ 文件已包含 MACD, KDJ, RSI, BOLL 等全套技术指标，可直接投喂给 Gemini。")
            
            # 5. 数据预览 (表格模式)
            st.markdown("### 📋 数据表内容")
            st.dataframe(
                df.sort_values('trade_date', ascending=False), 
                use_container_width=True, 
                height=600
            )
            
            with st.expander("查看处理日志"):
                st.write(logs)
