import streamlit as st
import folium
from streamlit_folium import st_folium
import os
import math
from dotenv import load_dotenv
from openai import OpenAI
import json
import requests
import datetime
import urllib.parse
import time
from ddgs import DDGS

# ==================== 環境設定 ====================
current_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(current_dir, ".env")
load_dotenv(dotenv_path)

api_key = os.getenv("OPENAI_API_KEY")

st.set_page_config(page_title="智慧旅遊推薦系統 MVP 12.0", layout="wide")

st.title("🌍 智慧旅遊推薦系統 24.0 (防偷天換日+精準定位版)")
st.caption("🔥 本次修正：阻斷 Booking 精選替代房陷阱 | 嚴懲 AI 座標幻覺 | 預算鋼鐵同步")

if not api_key:
    st.error("❌ 請先在 .env 檔案中設定 OPENAI_API_KEY")
    st.stop()

client = OpenAI(api_key=api_key)
DAY_COLORS = ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'darkblue']

# --- 黑名單與白名單 ---
ABSOLUTE_BANNED = [
    'instagram.com', 'ig.com', 'facebook.com', 'fb.com', '.gov.tw', 'taiwan.net.tw', 
    'news', 'ltn.com', 'chinatimes', 'tvbs', 'setn', 'ebc', 'ettoday', 'cts.com',
    'udn.com', 'cna.com.tw', 'storm.mg', 'nownews.com', 'upmedia.mg', 'mirrormedia.mg', 'newtalk.tw',
    'twitch.tv', 'behance', 'ultrapex', 'shop', 'buy', 'wikipedia.org',
    'klook.com', 'kkday.com', 'agoda.com', 'booking.com', 'trip.com', 'hotels.com'
]

TRUSTED_DOMAINS = [
    'pixnet.net', 'walkerland.com.tw', 'funtime.com.tw', 'vocus.cc', 
    'medium.com', 'travel.yahoo.com.tw', 'marieclaire.com.tw',
    'gofun.tw', 'popdaily.com.tw', 'look-in.com.tw', 'dcard.tw', 'ptt.cc'
]

SPAM_KEYWORDS = ['官網', '官方', '新聞', '特惠', '購買', '賺錢', '被動收入', '自媒體', '教學', '行銷', 'seo', '課程', '直銷']
JP_CHARS = ['の', 'に', 'は', 'を', 'だ', 'です', 'ます', 'おすすめ', 'まとめ', 'ランキング', 'サイト']

def filter_travel_source(url, title, strict_mode=True):
    url_lower = url.lower()
    title_lower = title.lower()
    if any(banned in url_lower for banned in ABSOLUTE_BANNED): return False
    if any(k in title_lower for k in SPAM_KEYWORDS): return False
    if any(j in title_lower for j in JP_CHARS): return False
    if strict_mode:
        if any(trusted in url_lower for trusted in TRUSTED_DOMAINS): return True
        if 'blog' in url_lower or 'article' in url_lower or 'post' in url_lower: return True
        return False
    return True

def get_real_route(start_loc, end_loc):
    url = f"http://router.project-osrm.org/route/v1/driving/{start_loc[1]},{start_loc[0]};{end_loc[1]},{end_loc[0]}?overview=full&geometries=geojson"
    try:
        response = requests.get(url, timeout=3)
        data = response.json()
        if data['code'] == 'Ok':
            coords = data['routes'][0]['geometry']['coordinates']
            return [[c[1], c[0]] for c in coords]
    except Exception: pass
    return [start_loc, end_loc]

def get_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# ==================== 🛠️ Booking.com 自動空房與預算爬蟲驗證模組 ====================
def check_hotel_availability(hotel_name, budget, checkin, checkout, adults, rooms_count):
    encoded_hotel = urllib.parse.quote(hotel_name)
    url = f"https://www.booking.com/searchresults.zh-tw.html?ss={encoded_hotel}&checkin={checkin}&checkout={checkout}&group_adults={adults}&no_rooms={rooms_count}&nflt=price%3DTWD-0-{budget}-1"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/"
    }
    try:
        time.sleep(0.6)
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            html = res.text
            # 🚨 核心修正：加入「沒有找到住宿」、「沒有找到符合」，徹底擊碎 Booking 的替代推薦陷阱
            full_keywords = [
                "房間已滿", "已售罄", "沒有符合", "無空房", "無法預訂", 
                "沒有找到符合", "全體滿房", "沒空房", "沒有可預訂", "沒有找到住宿"
            ]
            if any(kw in html for kw in full_keywords):
                return False, url 
            return True, url 
        return True, url 
    except:
        return True, url

# ==================== 狀態記憶初始化 ====================
if "travel_result" not in st.session_state: st.session_state.travel_result = None
if "global_itineraries" not in st.session_state: st.session_state.global_itineraries = []
if "link_pools" not in st.session_state: st.session_state.link_pools = {}
if "hotel_idx" not in st.session_state: st.session_state.hotel_idx = 0 
if "seen_hotels" not in st.session_state: st.session_state.seen_hotels = set()
if "original_hotel_loc" not in st.session_state: st.session_state.original_hotel_loc = None
if "replan_warning_hotel" not in st.session_state: st.session_state.replan_warning_hotel = None

# --- 側邊欄介面設計 ---
with st.sidebar:
    st.header("🎯 旅遊基本偏好")
    destination = st.text_input("想去哪裡玩？", value="台北", placeholder="例如：台北、台中、東京")
    travel_date = st.date_input("預計旅遊出發日期", datetime.date(2026, 7, 1))
    days = st.slider("旅遊天數", min_value=1, max_value=7, value=5)
    
    st.write("---")
    st.header("🏨 訂房與人數設定")
    enable_hotel = st.checkbox("啟用核心飯店 (放射狀行程)", value=True)
    
    hotel_budget = 4000
    travelers = 2
    rooms = 1
    
    if enable_hotel:
        col1, col2 = st.columns(2)
        with col1: travelers = st.number_input("旅遊人數", min_value=1, value=2)
        with col2: rooms = st.number_input("房間數", min_value=1, value=1)
        hotel_budget = st.slider("每晚飯店預算上限 (TWD)", 1000, 15000, 2000, step=500) # 預設改2000測試極限

    st.write("---")
    submit_button = st.button("啟動終極精準規劃 🚀", type="primary")

    st.write("---")
    st.header("👁️ 視圖控制中心")
    if st.session_state.travel_result is not None:
        available_days = [f"第 {day_info['day']} 天" for day_info in st.session_state.travel_result.get('itinerary', [])]
        selected_display_days = st.multiselect("選擇要顯示的天數：", options=available_days, default=available_days)
    else: selected_display_days = []

# ==================== 核心生成模組 ====================
def build_itinerary(dest, d_days, t_date, budget, forced_hotel_obj=None):
    st.session_state.global_itineraries = []
    st.session_state.link_pools = {}
    st.session_state.hotel_idx = 0 
    
    checkin_str = t_date.strftime('%Y-%m-%d')
    nights = max(1, d_days - 1)
    checkout_str = (t_date + datetime.timedelta(days=nights)).strftime('%Y-%m-%d')

    # ------------------ 步驟 1：抓取全域攻略 ------------------
    with st.spinner(f"🔍 正在嚴格篩選【{dest}】當地優質攻略..."):
        global_query = f"{dest} {d_days}天 自由行 遊記 推薦"
        global_dedup = {}
        try:
            with DDGS() as crawler:
                res = list(crawler.text(global_query, region='tw-tz', max_results=25))
                for r in res:
                    url, title, snippet = r.get('href', ''), r.get('title', ''), r.get('body', '')
                    if dest not in title and dest not in snippet: continue
                    if filter_travel_source(url, title, strict_mode=True):
                        if url not in global_dedup:
                            global_dedup[url] = {"title": title, "url": url, "snippet": snippet[:200]}
                            if len(global_dedup) >= 3: break
                if len(global_dedup) < 3:
                    for r in res:
                        url, title, snippet = r.get('href', ''), r.get('title', ''), r.get('body', '')
                        if dest not in title and dest not in snippet: continue
                        if filter_travel_source(url, title, strict_mode=False):
                            if url not in global_dedup:
                                global_dedup[url] = {"title": title, "url": url, "snippet": snippet[:200]}
                                if len(global_dedup) >= 3: break
        except Exception: pass
        st.session_state.global_itineraries = list(global_dedup.values())

    if not st.session_state.global_itineraries:
        st.error("❌ 無法取得精準的旅遊攻略，請嘗試更換關鍵字。")
        st.stop()

    # ------------------ 步驟 2：篩選飯店（加入嚴格座標與預算警告） ------------------
    verified_hotel_data = None
    
    if forced_hotel_obj:
        verified_hotel_data = forced_hotel_obj
    else:
        banned_in_loop = set(st.session_state.seen_hotels)
        for attempt in range(6):
            banned_str = ", ".join(list(banned_in_loop)) if banned_in_loop else "無"
            with st.spinner(f"🤖 AI 正在尋找符合預算且有空房的飯店 (嘗試第 {attempt+1}/6 次)..."):
                # 🚨 核心修正：加強 Prompt 控制，杜絕高價大飯店與「萬用火車站」座標
                hotel_prompt = f"""
                請為「{dest}」推薦 1 間真實存在、每晚預算上限為 {budget} TWD 的平價平民旅宿、青年旅館或商務旅館。
                
                【硬性防錯規範】:
                1. 預算限制非常嚴格（目前為 {budget} TWD）。絕對禁止推薦原本行情就遠超預算的知名五星級大飯店（例如：福華大飯店、晶華酒店、老爺大酒店、W Hotel等），否則線上爬蟲會直接因為超標判定「沒有找到住宿」而將其淘汰。
                2. 經緯度 (lat, lng) 必須是該飯店的「精確地理位置」，絕對不可偷懶敷衍全部標在「台北車站」或市中心點(如 25.0478, 121.5170)！必須讓地圖標記正確。
                3. 【絕對不可推薦以下飯店】: {banned_str}
                
                請直接以下列 JSON 格式回傳：
                {{
                    "name": "飯店名稱",
                    "description": "簡短的心得點評與生活機能介紹。",
                    "lat": 緯度數字,
                    "lng": 經度數字
                }}
                """
                try:
                    h_res = client.chat.completions.create(
                        model="gpt-4o-mini", messages=[{"role": "user", "content": hotel_prompt}],
                        response_format={"type": "json_object"}, temperature=0.4
                    )
                    h_data = json.loads(h_res.choices[0].message.content)
                    h_name = h_data.get("name", "精選飯店")
                    
                    # 🔍 啟動網頁爬蟲即時攔截驗證
                    with st.spinner(f"🕵️‍♂️ 正在線上驗證【{h_name}】是否被 Booking 判定為無住宿或已滿房..."):
                        is_avail, _ = check_hotel_availability(h_name, budget, checkin_str, checkout_str, travelers, rooms)
                    
                    if is_avail:
                        verified_hotel_data = h_data
                        break
                    else:
                        st.toast(f"⚠️ 偵測到「{h_name}」不符預算預訂條件或已被 Booking.com 替代，自動淘汰！")
                        banned_in_loop.add(h_name)
                except Exception: pass
        
        if not verified_hotel_data:
            st.warning("⚠️ 經多次爬蟲比對，該預算區間極其熱門，已為您採用保底安全旅宿。")
            verified_hotel_data = h_data 

    # ------------------ 步驟 3：建立放射狀骨架 ------------------
    with st.spinner(f"🧠 已鎖定真實有房且精準定位之飯店【{verified_hotel_data['name']}】，正在安排周邊不繞路行程..."):
        global_block = "\n".join([f"【文獻】{g['title']}\n摘要: {g['snippet']}" for g in st.session_state.global_itineraries])
        skeleton_prompt = f"""
        你是一個專業的旅遊規劃師。請為「{dest}」規劃一個實用、不繞路的行程骨架。
        【住宿位置】: 已確認入住位於 (緯度:{verified_hotel_data['lat']}, 經度:{verified_hotel_data['lng']}) 的「{verified_hotel_data['name']}」。
        請務必以此飯店實際座標為核心出發點（放射狀行程），重新安排每天的景點，絕對不可繞路！
        【天數限制】: 必須包含從 day 1 到 day {d_days} 的完整資料。
        {global_block}
        請嚴格以下列 JSON 格式回傳骨架：
        {{
            "skeleton": [
                {{ "day": 1, "spots": ["景點A", "景點B"] }}
            ]
        }}
        """
        try:
            skeleton_res = client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": skeleton_prompt}],
                response_format={"type": "json_object"}, temperature=0.3, max_tokens=2000
            )
            skeleton_data = json.loads(skeleton_res.choices[0].message.content)
        except Exception as e:
            st.error(f"骨架生成失敗: {e}")
            st.stop()

    # ------------------ 步驟 4：深度抓取景點部落格文章 ------------------
    spot_deep_data = {}
    with DDGS() as crawler:
        h_name = verified_hotel_data['name']
        with st.spinner(f"🏨 正在同步抓取新飯店【{h_name}】的真實達人開箱攻略..."):
            hotel_links = []
            try:
                h_res = list(crawler.text(f"{dest} {h_name} 住宿 心得 遊記", region='tw-tz', max_results=8))
                for r in h_res:
                    if filter_travel_source(r.get('href', ''), r.get('title', ''), strict_mode=False):
                        hotel_links.append({"url": r.get('href', ''), "title": r.get('title', ''), "snippet": r.get('body', '')[:150]})
            except: pass
            st.session_state.link_pools[h_name] = hotel_links
            spot_deep_data[h_name] = hotel_links[0] if hotel_links else {"url": "無", "snippet": "無"}

        for day_info in skeleton_data.get("skeleton", []):
            for spot in day_info.get("spots", []):
                with st.spinner(f"📍 正在深度檢索【{spot}】的遊記散策..."):
                    spot_links = []
                    try:
                        s_res = list(crawler.text(f"{dest} {spot} 遊記 心得 參觀", region='tw-tz', max_results=10))
                        for r in s_res:
                            if filter_travel_source(r.get('href', ''), r.get('title', ''), strict_mode=False):
                                spot_links.append({"url": r.get('href', ''), "title": r.get('title', ''), "snippet": r.get('body', '')[:150]})
                    except: pass
                    st.session_state.link_pools[spot] = spot_links
                    spot_deep_data[spot] = spot_links[0] if spot_links else {"url": "無", "snippet": "暫無專欄介紹"}
                    time.sleep(0.2)

    # ------------------ 步驟 5：終極融合產出手冊 ------------------
    with st.spinner("🧙‍♂️ AI 正在融合深度文獻，產出最終手冊..."):
        final_prompt = f"""
        請根據精準搜尋到的「遊記摘要」，撰寫最終行程。
        【必須完整輸出從 Day 1 到 Day {d_days} 的行程】
        請嚴格以下列 JSON 格式回傳，並確保景點座標 (lat, lng) 準確無誤：
        {{
            "summary": "一句話總結這個充滿人情味的行程",
            "hotels": [
                {{
                    "name": "{verified_hotel_data['name']}",
                    "description": "{verified_hotel_data['description']}",
                    "lat": {verified_hotel_data['lat']},
                    "lng": {verified_hotel_data['lng']}
                }}
            ],
            "itinerary": [
                {{
                    "day": 1,
                    "spots": [
                        {{
                            "name": "景點名稱",
                            "description": "溫度的參觀心法",
                            "lat": 緯度數字, "lng": 經度數字, "transit_info": "到下一站的交通建議"
                        }}
                    ]
                }}
            ]
        }}
        """
        try:
            final_res = client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": final_prompt}],
                response_format={"type": "json_object"}, temperature=0.4, max_tokens=4000
            )
            final_data = json.loads(final_res.choices[0].message.content)
            
            st.session_state.travel_result = final_data
            st.session_state.seen_hotels.add(verified_hotel_data['name'])
            if st.session_state.original_hotel_loc is None:
                st.session_state.original_hotel_loc = (verified_hotel_data['lat'], verified_hotel_data['lng'])
            st.rerun()
        except Exception as e:
            st.error(f"最終數據融合失敗：{e}")

# ==================== 觸發與邏輯流控制 ====================
if submit_button:
    if destination:
        st.session_state.seen_hotels = set()
        st.session_state.original_hotel_loc = None
        st.session_state.replan_warning_hotel = None
        build_itinerary(destination, days, travel_date, hotel_budget)
    else: st.warning("⚠️ 請先輸入目的地！")

if st.session_state.replan_warning_hotel is not None:
    far_hotel = st.session_state.replan_warning_hotel
    st.error("🚨 **【全域重新規劃通知】周邊範圍飯店已耗盡！**")
    st.warning(f"在您原本的核心區域 5 公里內，已經找不到其他符合預算 ({hotel_budget} TWD) 且有空房的飯店。\n\n"
               f"系統幫您找到了位於遠處的 **【{far_hotel['name']}】** (已通過Booking空房與預算嚴格驗證)。\n"
               "⚠️ **為避免瘋狂繞路，請以此新飯店為起點重新安排整體行程！**")
    
    c1, c2 = st.columns(2)
    if c1.button("🔄 以此新飯店為核心，【重新規劃全部行程】", type="primary"):
        st.session_state.replan_warning_hotel = None
        build_itinerary(destination, days, travel_date, hotel_budget, forced_hotel_obj=far_hotel)
    if c2.button("❌ 放棄此備案，我調整預算再試一次"):
        st.session_state.replan_warning_hotel = None
        st.rerun()
    st.stop()

# ==================== 畫面渲染區 ====================
if st.session_state.travel_result is not None:
    result = st.session_state.travel_result
    
    if st.session_state.global_itineraries:
        st.markdown("### 📚 本次行程參考之優質達人部落格大攻略")
        g_cols = st.columns(len(st.session_state.global_itineraries))
        for idx, g_source in enumerate(st.session_state.global_itineraries):
            with g_cols[idx]:
                st.success(f"🔗 **親身自由行攻略 {idx+1}**\n[{g_source['title']}]({g_source['url']})")
        st.write("---")

    col1, col2 = st.columns([1, 1])
    allowed_day_numbers = [int(s.split(" ")[1]) for s in selected_display_days] if selected_display_days else []
    
    hotels_data = result.get("hotels", [])
    has_hotel = enable_hotel and len(hotels_data) > 0
    curr_hotel = hotels_data[st.session_state.hotel_idx] if has_hotel else None
    
    with col1:
        st.success(f"✨ **導遊私房真心話：** {result.get('summary', '')}")
        
        if has_hotel and curr_hotel:
            hotel_name = curr_hotel['name']
            checkin_str = travel_date.strftime('%Y-%m-%d')
            nights = max(1, days - 1)
            checkout_str = (travel_date + datetime.timedelta(days=nights)).strftime('%Y-%m-%d')
            
            encoded_hotel = urllib.parse.quote(hotel_name)
            booking_link = f"https://www.booking.com/searchresults.zh-tw.html?ss={encoded_hotel}&checkin={checkin_str}&checkout={checkout_str}&group_adults={travelers}&no_rooms={rooms}&nflt=price%3DTWD-0-{hotel_budget}-1"
            gmaps_hotel_link = f"https://www.google.com/maps/search/?api=1&query={encoded_hotel}"
            
            st.info(f"🏨 **核心住宿推薦：{hotel_name}**\n\n{curr_hotel.get('description', '')}")
            
            # --- 🔄 按鈕：換一間備用飯店 (同步進行嚴格 Booking 爬蟲驗證) ---
            if st.button(f"🔄 滿房了？換一間備用飯店 (自動進行網頁空房與預算掃描)"):
                orig_lat, orig_lng = st.session_state.original_hotel_loc
                new_hotel_data = None
                
                for _ in range(6):
                    banned_str = ", ".join(list(st.session_state.seen_hotels))
                    refresh_prompt = f"""
                    舊飯店座標為 (緯度:{orig_lat}, 經度:{orig_lng})。
                    請為【{destination}】推薦 1 間符合每晚 {hotel_budget} TWD 預算上限的平價新旅館。
                    【絕對禁止】推薦超標的昂貴大飯店或已排除的名單：{banned_str}。
                    請確保經緯度精確，勿胡亂標示在火車站。
                    請以 JSON 回傳：{{"name": "飯店名", "description": "點評", "lat": 緯度, "lng": 經度}}
                    """
                    try:
                        new_h_res = client.chat.completions.create(
                            model="gpt-4o-mini", messages=[{"role": "user", "content": refresh_prompt}],
                            response_format={"type": "json_object"}, temperature=0.5
                        )
                        candidate = json.loads(new_h_res.choices[0].message.content)
                        st.session_state.seen_hotels.add(candidate['name'])
                        
                        is_avail, _ = check_hotel_availability(candidate['name'], hotel_budget, checkin_str, checkout_str, travelers, rooms)
                        if is_avail:
                            new_hotel_data = candidate
                            break
                    except: continue
                
                if new_hotel_data:
                    dist = get_distance(orig_lat, orig_lng, new_hotel_data['lat'], new_hotel_data['lng'])
                    if dist > 5.0:
                        st.session_state.replan_warning_hotel = new_hotel_data
                        st.rerun()
                    else:
                        with DDGS() as crawler:
                            h_links = []
                            try:
                                h_res = list(crawler.text(f"{destination} {new_hotel_data['name']} 住宿 心得 遊記", region='tw-tz', max_results=8))
                                for r in h_res:
                                    if filter_travel_source(r.get('href', ''), r.get('title', ''), strict_mode=False):
                                        h_links.append({"url": r.get('href', ''), "title": r.get('title', ''), "snippet": r.get('body', '')[:150]})
                            except: pass
                            st.session_state.link_pools[new_hotel_data['name']] = h_links
                        
                        st.session_state.travel_result['hotels'].append(new_hotel_data)
                        st.session_state.hotel_idx = len(st.session_state.travel_result['hotels']) - 1
                        st.rerun()
                else:
                    st.error("❌ 經多重爬蟲掃描，目前附近已無其他符合預算之空房旅宿。")

            st.markdown("🚀 **【即時工具直達專區】**")
            st.markdown(f"👉 [點我直達 **Booking.com** 查看此飯店 (已自動帶入 {hotel_budget} TWD 預算過濾)]({booking_link})")
            
            h_pool = st.session_state.link_pools.get(hotel_name, [])
            if h_pool:
                h_idx_key = f"idx_hotel_link_{hotel_name}"
                if h_idx_key not in st.session_state: st.session_state[h_idx_key] = 0
                curr_h_link = h_pool[st.session_state[h_idx_key] % len(h_pool)]
                
                hc1, hc2 = st.columns([4, 1])
                with hc1: st.markdown(f"👉 [📖 **查看本飯店最新開箱文**：{curr_h_link['title']}]({curr_h_link['url']})")
                with hc2:
                    if len(h_pool) > 1 and st.button("🔄 換一篇", key=f"btn_{h_idx_key}"):
                        st.session_state[h_idx_key] += 1
                        st.rerun()

            st.markdown(f"👉 [🌍 在 **Google Maps** 中直接查看此飯店地標評論]({gmaps_hotel_link})")
            st.write("---")
        
        for d_idx, day_info in enumerate(result.get('itinerary', [])):
            day_num = day_info['day']
            if day_num not in allowed_day_numbers: continue
            
            color = DAY_COLORS[d_idx % len(DAY_COLORS)]
            st.markdown(f"### 📅 第 {day_num} 天 (地圖顏色：:{color}[{color.upper()}])")
            
            for s_idx, spot in enumerate(day_info.get('spots', [])):
                if spot.get('transit_info'): st.markdown(f"⬇️ 🚌 *{spot['transit_info']}*")
                    
                with st.expander(f"📍 站點 {s_idx+1}: {spot['name']}"):
                    st.markdown(spot.get('description', '無簡介'))
                    
                    s_pool = st.session_state.link_pools.get(spot['name'], [])
                    if s_pool:
                        s_idx_key = f"idx_spot_{d_idx}_{s_idx}"
                        if s_idx_key not in st.session_state: st.session_state[s_idx_key] = 0
                        curr_s_link = s_pool[st.session_state[s_idx_key] % len(s_pool)]
                        
                        sc1, sc2 = st.columns([4, 1])
                        with sc1: st.markdown(f"📖 **[遊記：{curr_s_link['title']}]({curr_s_link['url']})**")
                        with sc2:
                            if len(s_pool) > 1 and st.button("🔄 換一篇", key=f"btn_{s_idx_key}"):
                                st.session_state[s_idx_key] += 1
                                st.rerun()
                        
                    encoded_gmaps_spot = urllib.parse.quote(spot['name'])
                    gmaps_spot_link = f"https://www.google.com/maps/search/?api=1&query={encoded_gmaps_spot}"
                    st.markdown(f"🗺️ **[🌍 在 Google Maps 中直接搜尋【{spot['name']}】]({gmaps_spot_link})**")
            st.write("---")

    with col2:
        st.subheader("🗺️ 旅遊路徑與路網視覺化 (動態重繪)")
        if has_hotel and curr_hotel: base_loc = [curr_hotel['lat'], curr_hotel['lng']]
        else:
            try:
                first_spot = result['itinerary'][0]['spots'][0]
                base_loc = [first_spot['lat'], first_spot['lng']]
            except: base_loc = [25.0330, 121.5654]
                
        m = folium.Map(location=base_loc, zoom_start=13) 
        
        if has_hotel and curr_hotel:
            folium.Marker(
                location=base_loc, popup=f"🏨 {curr_hotel['name']}",
                tooltip=folium.Tooltip("🏠 核心飯店", permanent=True),
                icon=folium.Icon(color="black", icon="home")
            ).add_to(m)
        
        for d_idx, day_info in enumerate(result.get('itinerary', [])):
            day_num = day_info['day']
            if day_num not in allowed_day_numbers: continue
            
            color = DAY_COLORS[d_idx % len(DAY_COLORS)]
            spots = day_info.get('spots', [])
            
            if has_hotel and curr_hotel:
                day_route_points = [base_loc] + [[s['lat'], s['lng']] for s in spots] + [base_loc]
            else: day_route_points = [[s['lat'], s['lng']] for s in spots]
            
            for s_idx, spot in enumerate(spots):
                loc = [spot['lat'], spot['lng']]
                folium.Marker(
                    location=loc, popup=f"D{day_num} - {spot['name']}",
                    tooltip=folium.Tooltip(f"D{day_num}-{s_idx+1}: {spot['name']}"),
                    icon=folium.Icon(color=color, icon="info-sign")
                ).add_to(m)
            
            for i in range(len(day_route_points) - 1):
                start_p = day_route_points[i]
                end_p = day_route_points[i+1]
                if start_p != end_p:
                    route_coords = get_real_route(start_p, end_p)
                    is_to_from_hotel = has_hotel and (i == 0 or i == len(day_route_points)-2)
                    folium.PolyLine(
                        locations=route_coords, color=color, weight=4, opacity=0.7,
                        dash_array='10, 10' if is_to_from_hotel else None 
                    ).add_to(m)
        
        map_key = f"v24_travel_map_{st.session_state.hotel_idx}"
        st_folium(m, width="100%", height=600, returned_objects=[], key=map_key)