import streamlit as st
import pandas as pd
import akshare as ak
import plotly.graph_objects as go
import datetime
import pytz
import time
import random

# ----------------------------------------------------------------------------- 
# 0. 全局配置
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Hunter Data Fetcher (Smart)",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------------- 
# 1. 核心辅助函数
# -----------------------------------------------------------------------------
def get_symbol_prefix(code):
    """自动补充代码前缀 (用于新浪/腾讯接口)"""
    if not code or not isinstance(code, str): return code
    if code.startswith('6'): return f"sh{code}"
    if code.startswith('0') or code.startswith('3'): return f"sz{code}"
    if code.startswith('8') or code.startswith('4'): return f"bj{code}"
    return code

def calculate_macd(df, short=12, long=26, mid=9):
    """计算 MACD 指标"""
    close = df['close']
    ema12 = close.ewm(span=short, adjust=False).mean()
    ema26 = close.ewm(span=long, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=mid, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif, dea, macd

def clean_data(df, col_map):
    """标准化数据列名"""
    df = df.rename(columns=col_map)
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for c in numeric_cols:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

# ----------------------------------------------------------------------------- 
# 2. 智能名称搜索逻辑 (双向索引 + 强制回退)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def get_market_maps():
    """
    获取全市场映射表 (代码->名称, 名称->代码)
    """
    code2name = {}
    name2code = {}
    try:
        # 尝试接口 1: A股列表
        df = ak.stock_info_a_code_name()
        df['code'] = df['code'].astype(str).str.strip()
        df['name'] = df['name'].astype(str).str.strip()
        code2name = dict(zip(df['code'], df['name']))
        name2code = dict(zip(df['name'], df['code']))
    except Exception:
        pass
    
    return code2name, name2code

def smart_search(query, code2name, name2code):
    """
    智能搜索：支持代码或名称
    返回: (code, name, is_found)
    """
    query = str(query).strip()
    
    # 1. 如果是6位数字，优先当做代码查
    if query.isdigit() and len(query) == 6:
        if query in code2name:
            return query, code2name[query], True
        else:
            # 本地列表没找到，可能是漏了，尝试强制联网查个股信息
            try:
                # 强制回退机制：直接查个股资料
                df_info = ak.stock_individual_info_em(symbol=query)
                info = dict(zip(df_info['item'], df_info['value']))
                real_name = info.get('股票简称', query)
                return query, real_name, True
            except:
                return query, "未识别股票", False

    # 2. 否则当做中文名称查
    if query in name2code:
        return name2code[query], query, True
        
    # 3. 模糊搜索 (比如输入 "平安")
    # 只有当 query 包含中文时才模糊搜
    for name, code in name2code.items():
        if query in name:
            return code, name, True
            
    return query, "未知", False

# ----------------------------------------------------------------------------- 
# 3. 历史行情获取逻辑 (多源轮询)
# -----------------------------------------------------------------------------
def strategy_em(code, s, e):
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data(df, {'日期': 'trade_date', '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low', '成交量': 'volume', '换手率': 'turnover', '涨跌幅': 'pct_change'})

def strategy_sina(code, s, e):
    sym = get_symbol_prefix(code)
    df = ak.stock_zh_a_daily(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data(df, {'date': 'trade_date'})

def strategy_tencent(code, s, e):
    sym = get_symbol_prefix(code)
    df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=s, end_date=e, adjust="qfq")
    if df is None or df.empty: raise ValueError("Empty")
    return clean_data(df, {'date': 'trade_date'})

@st.cache_data(ttl=300)
def get_stock_history(code, days):
    logs = []
    
    # 日期计算
    end_dt = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)
    s_str, e_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    
    # 轮询策略
    strategies = [("EastMoney", strategy_em), ("Sina", strategy_sina), ("Tencent", strategy_tencent)]
    
    df = None
    for name, func in strategies:
        try:
            time.sleep(random.uniform(0.1, 0.3))
            temp_df = func(code, s_str, e_str)
            if temp_df is not None and not temp_df.empty:
                df = temp_df
                logs.append(f"✅ 数据源: {name}")
                break
        except: continue
            
    if df is None: 
        return None, "无法获取历史数据，请检查代码或网络。", logs
    
    # 补全指标
    for ma in [5, 10, 20, 60]: 
        df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()
    df['DIF'], df['DEA'], df['MACD'] = calculate_macd(df)
    
    # 补全基本信息列
    df['code'] = code
    
    return df, None, logs

# ----------------------------------------------------------------------------- 
# 4. 用户界面
# -----------------------------------------------------------------------------
st.sidebar.title("Hunter Data Fetcher")
st.sidebar.caption("支持代码或名称搜索 (如: 002860 或 星帅尔)")
st.sidebar.markdown("---")

# 1. 预加载全市场名称映射
with st.spinner("正在加载市场列表..."):
    code_map, name_map = get_market_maps()

# 2. 输入区
query_input = st.sidebar.text_input("输入代码或名称", value="002860")
lookback = st.sidebar.slider("查询回溯天数", 30, 1000, 365)

# 3. 智能识别与反馈
target_code, target_name, is_found = smart_search(query_input, code_map, name_map)

if is_found:
    st.sidebar.success(f"已锁定: **{target_name} ({target_code})**")
else:
    if query_input:
        st.sidebar.warning(f"本地列表未找到 '{query_input}'，尝试强制查询...")
        # 如果是6位数字，我们还是允许它作为代码去尝试
        if query_input.isdigit() and len(query_input) == 6:
            target_code = query_input
            target_name = "未知股票" # 暂时标记，查询成功后会更新
        else:
            target_code = None

st.sidebar.markdown("---")

# 4. 查询按钮
if st.sidebar.button("开始查询", type="primary"):
    
    if not target_code:
        st.error("❌ 无效的输入，请输入 6 位股票代码或正确的中文简称。")
    else:
        with st.spinner(f"正在获取 【{target_name}】 ({target_code}) 的数据..."):
            df, err, logs = get_stock_history(target_code, lookback)
        
        if err:
            st.error(f"❌ 获取失败: {err}")
            with st.expander("查看详细日志"):
                st.write(logs)
        else:
            # 如果之前没识别出名字（强制查询的情况），现在再尝试更新一次名字
            if target_name in ["未识别股票", "未知股票", "未知"]:
                # 尝试从 akshare 个股信息接口再次确认
                try:
                    info_df = ak.stock_individual_info_em(symbol=target_code)
                    info_dict = dict(zip(info_df['item'], info_df['value']))
                    target_name = info_dict.get('股票简称', target_name)
                except:
                    pass
            
            # 注入名称到 DataFrame
            df['name'] = target_name
            
            # 界面展示
            st.success(f"获取成功: {target_name} ({target_code})")
            
            # 获取最新数据用于展示
            last_row = df.iloc[-1]
            last_date = last_row['trade_date'].strftime("%Y-%m-%d")
            close_price = last_row['close']
            
            # 计算简单的涨跌幅展示
            pct_display = 0.0
            if 'pct_change' in df.columns:
                pct_display = last_row['pct_change']
            elif len(df) > 1:
                prev_close = df.iloc[-2]['close']
                pct_display = (close_price - prev_close) / prev_close * 100
                
            color = "red" if pct_display > 0 else "green"
            
            # 顶部指标栏
            c1, c2, c3 = st.columns(3)
            c1.metric("股票名称", target_name)
            c2.markdown(f"#### 收盘价: <span style='color:{color}'>{close_price}</span>", unsafe_allow_html=True)
            c3.markdown(f"#### 日期: {last_date}", unsafe_allow_html=True)
            
            st.markdown("---")
            
            # --- 下载功能 (文件名修复) ---
            # 格式: 【股票中文名称_时间】.csv
            file_time = datetime.datetime.now().strftime("%Y%m%d")
            
            # 再次确保文件名中没有非法字符
            safe_name = target_name.replace("*", "").replace(":", "") 
            file_name = f"【{safe_name}_{file_time}】.csv"
            
            csv_data = df.to_csv(index=False).encode('utf-8-sig')
            
            st.download_button(
                label=f"📥 下载数据: {file_name}",
                data=csv_data,
                file_name=file_name,
                mime="text/csv",
                type="primary"
            )
            
            # 预览图表
            with st.expander("📊 数据预览", expanded=True):
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=df['trade_date'], open=df['open'], high=df['high'], 
                    low=df['low'], close=df['close'], name='K线'
                ))
                for ma in [20, 60]:
                    if f'MA{ma}' in df:
                        fig.add_trace(go.Scatter(x=df['trade_date'], y=df[f'MA{ma}'], line=dict(width=1), name=f'MA{ma}'))
                
                fig.update_layout(height=450, xaxis_rangeslider_visible=False, title=f"{target_name} ({target_code}) 走势图")
                st.plotly_chart(fig, use_container_width=True)
