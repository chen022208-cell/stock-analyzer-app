import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import datetime
import time
import re
from bs4 import BeautifulSoup

# ==========================================
# ⚙️ 設定與快取
# ==========================================
st.set_page_config(
    page_title="股市指揮所 (戰略版)",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed" # 手機版預設收起側邊欄
)

# 模擬 User-Agent
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# ==========================================
# 🔧 工具函式
# ==========================================
def get_last_trading_date():
    today = datetime.date.today()
    d = today
    while d.weekday() > 4:
        d -= datetime.timedelta(days=1)
    now = datetime.datetime.now()
    if d == today and now.hour < 15:
        d -= datetime.timedelta(days=1)
        while d.weekday() > 4:
            d -= datetime.timedelta(days=1)
    return d

def safe_int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except:
        return 0

# ==========================================
# 📥 資料獲取 (使用 st.cache_data 加速)
# ==========================================
@st.cache_data(ttl=3600) # 快取 1 小時
def get_stock_db():
    """建立全台股代碼與名稱對照表 (上市+上櫃)"""
    stock_map = {} # Name -> Code
    code_map = {}  # Code -> Name
    
    urls = [
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", # 上市
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"  # 上櫃
    ]
    
    session = requests.Session()
    for url in urls:
        try:
            r = session.get(url, headers=HEADERS, timeout=10)
            r.encoding = 'big5'
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.find_all("tr")
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) < 1: continue
                txt = cols[0].get_text(strip=True)
                if "　" in txt:
                    code, name = txt.split("　", 1)
                    code = code.strip()
                    name = name.strip()
                    if len(code) == 4 and code.isdigit():
                        stock_map[name] = code
                        code_map[code] = name
        except: pass
    return stock_map, code_map

@st.cache_data(ttl=1800) # 快取 30 分鐘
def get_daily_chips():
    """抓取當日法人籌碼 (上市+上櫃)"""
    date = get_last_trading_date()
    chips_data = {}
    
    # 1. 上市
    try:
        d_str = date.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALLBUT0999&response=json"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if res.get("stat") == "OK":
            fields = res.get('fields', [])
            # 動態找索引
            idx_f = next((i for i, f in enumerate(fields) if '外陸資買賣超' in f), 4)
            idx_t = next((i for i, f in enumerate(fields) if '投信買賣超' in f), 10)
            idx_d_hedge = next((i for i, f in enumerate(fields) if '避險' in f), None)
            
            for row in res['data']:
                code = row[0]
                dh = safe_int(row[idx_d_hedge]) // 1000 if idx_d_hedge else 0
                f_buy = safe_int(row[idx_f]) // 1000
                t_buy = safe_int(row[idx_t]) // 1000
                # 簡單計算自營(包含避險)
                # 這裡為了效率簡化，若需精確 total_net 需加總
                chips_data[code] = {
                    'foreign': f_buy,
                    'trust': t_buy,
                    'dealer_hedge': dh,
                    'market': '上市'
                }
    except: pass

    # 2. 上櫃
    try:
        d_str = f"{date.year-1911}/{date.strftime('%m/%d')}"
        url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={d_str}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if 'aaData' in res:
            for row in res['aaData']:
                code = str(row[0])
                # 上櫃格式固定: [4]外資 [7]投信 [9]自營避險 [10]自營
                f_buy = safe_int(row[4]) // 1000
                t_buy = safe_int(row[7]) // 1000
                dh = safe_int(row[9]) // 1000
                chips_data[code] = {
                    'foreign': f_buy,
                    'trust': t_buy,
                    'dealer_hedge': dh,
                    'market': '上櫃'
                }
    except: pass
    
    return chips_data

def get_realtime_quote(code, market):
    """取得即時報價與技術指標"""
    suffix = ".TW" if market == "上市" else ".TWO"
    try:
        stock = yf.Ticker(f"{code}{suffix}")
        # 抓取 1 年資料算年線
        df = stock.history(period="1y") 
        if df.empty: return None
        
        current_price = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        change_pct = (current_price - prev_close) / prev_close * 100
        
        # 均線
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        ma240 = df['Close'].rolling(240).mean().iloc[-1] if len(df) >= 240 else None
        
        # 量能
        vol_ratio = 0
        if len(df) >= 6:
            vol_avg = df['Volume'].iloc[-6:-1].mean()
            vol_now = df['Volume'].iloc[-1]
            vol_ratio = vol_now / vol_avg if vol_avg > 0 else 0

        return {
            'price': current_price,
            'pct': change_pct,
            'ma5': ma5,
            'ma20': ma20,
            'ma60': ma60,
            'ma240': ma240,
            'vol_ratio': vol_ratio,
            'volume': df['Volume'].iloc[-1]
        }
    except: return None

# ==========================================
# 🧠 核心策略邏輯 (權重計算)
# ==========================================
def calculate_score(chips, tech):
    score = 60 # 基礎分
    reasons = []
    badges = []
    
    # --- 1. 籌碼面 (權重 40%) ---
    if chips:
        f = chips['foreign']
        t = chips['trust']
        d_h = chips['dealer_hedge']
        
        # 投信 (權重最重)
        if t > 0:
            s = min(15, t // 50) # 最多加15分
            score += s
            if t > 500: badges.append("🏦 投信大買")
        elif t < 0:
            score -= 5
            
        # 外資
        if f > 0:
            s = min(10, f // 200) # 最多加10分
            score += s
            if f > 2000: badges.append("💰 外資重倉")
        elif f < -1000:
            score -= 5
            
        # 🚩 假外資/隔日沖策略 (負面權重)
        # 外資大買 + 股價不漲(或微漲) + 自營避險大賣
        if f > 1000 and tech and tech['pct'] < 1.0 and d_h < -200:
            score -= 20
            badges.append("⚠️ 疑似假外資")
            reasons.append("外資買不動且避險倒貨")
            
        # 土洋對作
        if f > 500 and t < -100:
            score -= 5
            reasons.append("土洋對作")

    # --- 2. 技術面 (權重 40%) ---
    if tech:
        p = tech['price']
        
        # 均線多頭
        if p > tech['ma20']:
            score += 10
            reasons.append("站上月線")
        else:
            score -= 5
            
        if tech['ma240'] and p > tech['ma240']:
            score += 5
            badges.append("🐂 長多格局")
            
        # 動能
        if tech['vol_ratio'] > 1.5 and tech['pct'] > 0:
            score += 5
            reasons.append("量增價漲")
            
        # 乖離過大扣分
        if tech['pct'] > 9.0:
            score -= 5
            reasons.append("漲停過熱")

    return max(0, min(100, int(score))), badges, reasons

# ==========================================
# 📱 頁面 UI
# ==========================================

# 1. 側邊欄搜尋
st.sidebar.header("🔍 股票搜尋")
stock_map, code_map = get_stock_db()
chips_db = get_daily_chips()

# 建立搜尋選單 (代號 + 名稱)
search_list = [f"{code} {name}" for code, name in code_map.items()]
selected_stock = st.sidebar.selectbox("輸入代號或名稱", [""] + search_list)

# 2. 主畫面 Tabs
tab1, tab2 = st.tabs(["📊 個股戰略", "🏆 飆股排行"])

# --- Tab 1: 個股戰略 ---
with tab1:
    if selected_stock:
        code = selected_stock.split(" ")[0]
        name = selected_stock.split(" ")[1]
        
        # 顯示標題
        st.title(f"{name} ({code})")
        
        # 獲取資料
        chips = chips_db.get(code)
        market = chips['market'] if chips else ("上市" if code in code_map else "上市")
        tech = get_realtime_quote(code, market)
        
        if tech:
            # 計算分數
            score, badges, reasons = calculate_score(chips, tech)
            
            # --- 頂部指標區 ---
            col1, col2, col3 = st.columns(3)
            
            # 股價與漲跌
            color = "red" if tech['pct'] > 0 else "green"
            with col1:
                st.metric("現價", f"{tech['price']}", f"{tech['pct']:.2f}%")
            
            # 分數 (儀表板概念)
            with col2:
                st.metric("戰略評分", f"{score} 分", help="結合籌碼與技術面的綜合評分")
            
            # 量比
            with col3:
                st.metric("量比", f"{tech['vol_ratio']:.1f} 倍", "放量" if tech['vol_ratio']>1.5 else "正常")

            # --- 標籤區 ---
            if badges:
                st.write(" ".join([f"`{b}`" for b in badges]))
            
            # --- 詳細數據面板 ---
            st.divider()
            
            # 技術面卡片
            st.subheader("📈 技術關鍵位")
            t_col1, t_col2, t_col3 = st.columns(3)
            t_col1.info(f"**MA20 (月線)**\n\n{tech['ma20']:.2f}")
            t_col2.info(f"**MA60 (季線)**\n\n{tech['ma60']:.2f}")
            ma240_val = f"{tech['ma240']:.2f}" if tech['ma240'] else "無"
            t_col3.info(f"**MA240 (年線)**\n\n{ma240_val}")
            
            # 籌碼面卡片
            st.subheader("🏦 法人動向 (今日)")
            if chips:
                c_col1, c_col2, c_col3 = st.columns(3)
                
                f_val = chips['foreign']
                t_val = chips['trust']
                d_h_val = chips['dealer_hedge']
                
                c_col1.metric("外資", f"{f_val} 張", delta_color="normal")
                c_col2.metric("投信", f"{t_val} 張", delta_color="normal")
                c_col3.metric("自營避險", f"{d_h_val} 張", help="若大賣通常為隔日沖賣壓")
            else:
                st.warning("尚無今日法人籌碼資料")
            
            # 策略分析結論
            st.divider()
            st.subheader("🧠 策略診斷")
            if score >= 80:
                st.success(f"**強力多頭**：籌碼與技術面同步轉強。{','.join(reasons)}")
            elif score >= 60:
                st.info(f"**中性偏多**：表現穩健，持續觀察。{','.join(reasons)}")
            elif score < 40:
                st.error(f"**弱勢警戒**：建議避開或減碼。{','.join(reasons)}")
            else:
                st.warning(f"**震盪整理**：多空拉鋸中。{','.join(reasons)}")
                
        else:
            st.error("無法取得即時報價，請確認代號是否正確。")
    else:
        st.info("👈 請在側邊欄選擇或搜尋股票")

# --- Tab 2: 飆股排行 ---
with tab2:
    st.header("🏆 法人與策略飆股排行")
    
    if st.button("🔄 掃描全市場 (需耗時約 10-20 秒)"):
        with st.spinner("正在進行策略運算與過濾..."):
            # 準備清單
            candidates = []
            
            # 進度條
            progress_bar = st.progress(0)
            
            # 過濾與評分邏輯
            # 為了效能，我們先只篩選「法人大買」的股票，再來算技術分數
            raw_chips = chips_db.items()
            total_len = len(raw_chips)
            
            for i, (code, data) in enumerate(raw_chips):
                if i % 100 == 0: progress_bar.progress(min(i / total_len, 1.0))
                
                # 1. 排除條件
                if code.startswith('00'): continue # ETF
                if code.startswith('28') or code.startswith('58') or code.startswith('60'): continue # 金融
                
                # 2. 初步篩選 (外資 > 500 或 投信 > 100)
                if data['foreign'] < 500 and data['trust'] < 100: continue
                
                # 3. 取得名稱
                name = code_map.get(code, code)
                
                # 4. 簡易評分 (為了速度，這裡先不抓 yfinance，只看籌碼)
                # 若要看技術面，會非常慢，所以手機版排行通常只做籌碼排序
                score = 60
                if data['trust'] > 0: score += 20
                if data['foreign'] > 1000: score += 10
                
                # 假外資扣分 (籌碼面)
                if data['foreign'] > 1000 and data['dealer_hedge'] < -300:
                    score -= 15
                    name += " (⚠️)"
                
                candidates.append({
                    "代號": code,
                    "名稱": name,
                    "評分": score,
                    "外資": data['foreign'],
                    "投信": data['trust'],
                    "自營避險": data['dealer_hedge']
                })
            
            progress_bar.empty()
            
            # 轉 DataFrame 並排序
            df = pd.DataFrame(candidates)
            if not df.empty:
                df = df.sort_values(by="評分", ascending=False).head(30)
                
                # 顯示表格 (使用 st.dataframe 會有互動性)
                st.dataframe(
                    df,
                    column_config={
                        "評分": st.column_config.ProgressColumn(
                            "戰力評分",
                            help="基於法人籌碼的戰略評分",
                            format="%d",
                            min_value=0,
                            max_value=100,
                        ),
                        "外資": st.column_config.NumberColumn(format="%d 張"),
                        "投信": st.column_config.NumberColumn(format="%d 張"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.warning("今日無符合策略的強勢股")
                
    st.caption("註：排行僅基於收盤後籌碼進行策略篩選，⚠️標記代表有假外資嫌疑。")

# ==========================================
# 底部資訊
# ==========================================
st.markdown("---")
st.caption("🚀 AI 股市指揮所 (Mobile) | 資料來源: TWSE/TPEX/Yahoo | 僅供參考，不構成投資建議")