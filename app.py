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
google_api_key = os.getenv("GOOGLE_PLACES_API_KEY")

st.set_page_config(page_title="智慧旅遊推薦系統", layout="wide")

st.title("🌍 智慧旅遊推薦系統")
st.caption("✨ 結合 Google Maps 真實圖資與 AI 深度攻略 (GPT-4o 旅客真實體驗版)")

if not api_key:
    st.error("❌ 請先在 .env 檔案中設定 OPENAI_API_KEY")
    st.stop()
if not google_api_key:
    st.error("❌ 請先在 .env 檔案中設定 GOOGLE_PLACES_API_KEY")
    st.stop()

client = OpenAI(api_key=api_key)
DAY_COLORS = ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'darkblue']

# --- 黑名單與白名單 (大幅強化封殺訂房網與官方) ---
ABSOLUTE_BANNED = [
    'instagram.com', 'ig.com', 'facebook.com', 'fb.com', '.gov.tw', 'taiwan.net.tw', 
    'news', 'ltn.com', 'chinatimes', 'tvbs', 'setn', 'ebc', 'ettoday', 'cts.com',
    'udn.com', 'cna.com.tw', 'storm.mg', 'nownews.com', 'upmedia.mg', 'mirrormedia.mg', 'newtalk.tw',
    'twitch.tv', 'behance', 'ultrapex', 'shop', 'buy', 'wikipedia.org',
    'klook.com', 'kkday.com', 'agoda.com', 'booking.com', 'trip.com', 'hotels.com',
    'expedia', 'trivago', 'eztravel', 'liontravel', 'colatour', 'asiayo', 'momoshop'
]

TRUSTED_DOMAINS = [
    'pixnet.net', 'walkerland.com.tw', 'funtime.com.tw', 'vocus.cc', 
    'medium.com', 'travel.yahoo.com.tw', 'marieclaire.com.tw',
    'gofun.tw', 'popdaily.com.tw', 'look-in.com.tw', 'dcard.tw', 'ptt.cc', 'mobile01.com'
]

SPAM_KEYWORDS = ['官網', '官方', '新聞', '特惠', '購買', '賺錢', '被動收入', '自媒體', '教學', '行銷', 'seo', '課程', '直銷', '優惠碼']
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

# ==================== 🛠️ Google Places 飯店檢索模組 ====================
def search_real_hotels_google(dest, budget):
    search_keyword = "平價飯店 OR 青年旅館 OR 民宿" if budget <= 3000 else "飯店 OR 酒店"
    query = f"{dest} {search_keyword}"
    encoded_query = urllib.parse.quote(query)
    
    url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={encoded_query}&key={google_api_key}&language=zh-TW"
    
    hotels = []
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        if data.get('status') == 'OK':
            for r in data['results'][:6]:
                hotels.append({
                    "name": r.get('name'),
                    "lat": r['geometry']['location']['lat'],
                    "lng": r['geometry']['location']['lng'],
                    "rating": r.get('rating', '無評分'),
                    "address": r.get('formatted_address', '無地址')
                })
    except Exception as e:
        st.error(f"Google Places API 錯誤: {e}")
    
    return hotels

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
        hotel_budget = st.slider("每晚飯店預算參考 (TWD)", 1000, 15000, 2000, step=500)

    st.write("---")
    submit_button = st.button("啟動旅遊規劃 🚀", type="primary")

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
    
    with st.spinner(f"🔍 正在篩選【{dest}】當地優質達人遊記..."):
        global_query = f"{dest} {d_days}天 自由行 遊記 心得 推薦 blog"
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

    verified_hotel_data = None
    
    if forced_hotel_obj:
        verified_hotel_data = forced_hotel_obj
    elif enable_hotel:
        with st.spinner("🤖 正在從 Google Maps 檢索真實飯店資料..."):
            real_hotels = search_real_hotels_google(dest, budget)
            available_hotels = [h for h in real_hotels if h['name'] not in st.session_state.seen_hotels]
            
            if available_hotels:
                hotel_options_str = "\n".join([f"- {h['name']} (評分:{h['rating']}, 座標:{h['lat']},{h['lng']}, 地址:{h['address']})" for h in available_hotels])
                
                hotel_prompt = f"""
                以下是透過 Google Maps 取得「{dest}」真實存在的飯店清單：
                {hotel_options_str}
                
                請根據使用者的預算偏好（每晚約 {budget} TWD），從上述清單中挑選「1間」最適合的飯店。
                
                請嚴格以下列 JSON 格式回傳，座標必須「完全照抄」清單上的數據，不可自己發明：
                {{
                    "name": "挑選的飯店名稱",
                    "description": "簡短的推薦理由與周邊生活機能介紹。",
                    "lat": 緯度數字,
                    "lng": 經度數字
                }}
                """
                try:
                    h_res = client.chat.completions.create(
                        model="gpt-4o-mini", messages=[{"role": "user", "content": hotel_prompt}],
                        response_format={"type": "json_object"}, temperature=0.2
                    )
                    verified_hotel_data = json.loads(h_res.choices[0].message.content)
                except Exception: 
                    verified_hotel_data = available_hotels[0]
                    verified_hotel_data['description'] = f"這是一間位於 {dest} 的精選住宿，Google 評分 {verified_hotel_data['rating']}。"
            else:
                st.warning("⚠️ 附近找不到符合條件的新飯店，將採用預設中心點。")
                verified_hotel_data = {"name": "市區中心", "description": "預設起點", "lat": 25.0478, "lng": 121.5170}

    hotel_context = ""
    if enable_hotel and verified_hotel_data:
        hotel_context = f"【住宿位置】: 已確認入住位於 (緯度:{verified_hotel_data['lat']}, 經度:{verified_hotel_data['lng']}) 的「{verified_hotel_data['name']}」。請務必以此實際座標為核心出發點（放射狀行程），絕對不可繞路！"
        
    with st.spinner(f"🧠 正在安排不繞路的順路行程 (動用 GPT-4o 確保動線與分散度)..."):
        global_block = "\n".join([f"【文獻】{g['title']}\n摘要: {g['snippet']}" for g in st.session_state.global_itineraries])
        skeleton_prompt = f"""
        你是一個專業的旅遊規劃師。請為「{dest}」規劃一個實用的行程骨架。
        {hotel_context}
        【天數限制與空間要求】: 必須包含從 day 1 到 day {d_days} 的完整資料。且確保「景點不要太過集中」，每天應有合理的移動範圍與探索感。每天建議安排 3~4 個景點。
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
                model="gpt-4o", messages=[{"role": "user", "content": skeleton_prompt}],
                response_format={"type": "json_object"}, temperature=0.3, max_tokens=2000
            )
            skeleton_data = json.loads(skeleton_res.choices[0].message.content)
        except Exception as e:
            st.error(f"骨架生成失敗: {e}")
            st.stop()

    with DDGS() as crawler:
        if enable_hotel and verified_hotel_data:
            h_name = verified_hotel_data['name']
            with st.spinner(f"🏨 正在過濾官方網頁，專注抓取飯店【{h_name}】的真實心得/遊記..."):
                hotel_links = []
                try:
                    # 強制加上 blog 心得 遊記，過濾掉官方或訂房網
                    h_res = list(crawler.text(f"{dest} {h_name} 住宿 心得 遊記 blog", region='tw-tz', max_results=12))
                    for r in h_res:
                        if filter_travel_source(r.get('href', ''), r.get('title', ''), strict_mode=False):
                            hotel_links.append({"url": r.get('href', ''), "title": r.get('title', ''), "snippet": r.get('body', '')[:150]})
                except: pass
                st.session_state.link_pools[h_name] = hotel_links

        for day_info in skeleton_data.get("skeleton", []):
            for spot in day_info.get("spots", []):
                with st.spinner(f"📍 正在過濾官方資訊，深度檢索【{spot}】的鄉民評價與遊記..."):
                    spot_links = []
                    try:
                        s_res = list(crawler.text(f"{dest} {spot} 遊記 心得 評價 blog", region='tw-tz', max_results=12))
                        for r in s_res:
                            if filter_travel_source(r.get('href', ''), r.get('title', ''), strict_mode=False):
                                spot_links.append({"url": r.get('href', ''), "title": r.get('title', ''), "snippet": r.get('body', '')[:150]})
                    except: pass
                    st.session_state.link_pools[spot] = spot_links
                    time.sleep(0.2)

    with st.spinner("🧙‍♂️ AI 正在融合真實文獻，完整產出所有行程內容 (確保不偷懶)..."):
        hotels_json = ""
        if enable_hotel and verified_hotel_data:
            hotels_json = f"""
            "hotels": [
                {{
                    "name": "{verified_hotel_data['name']}",
                    "description": "{verified_hotel_data['description']}",
                    "lat": {verified_hotel_data['lat']},
                    "lng": {verified_hotel_data['lng']}
                }}
            ],
            """
        else:
            hotels_json = '"hotels": [],'
            
        references_text = ""
        for s_name, links in st.session_state.link_pools.items():
            if links:
                references_text += f"[{s_name} 鄉民評價/遊記摘要]: {links[0]['title']} - {links[0]['snippet']}\n"

        final_prompt = f"""
        請根據以下精準搜尋到的「真實遊記摘要」，撰寫最終行程。
        
        遊記摘要參考：
        {references_text}
        
        【要求一：真實網路評價與拒絕官方網址】
        景點不可提供官方網站連結！請專注於結合遊記產出「web_intro（網路介紹）」，以鄉民、旅客的真實口吻說明這個景點為什麼好玩、有什麼雷區或必吃必看重點。

        【要求二：絕不偷懶，完整生成】
        你必須「完整產出」 Day 1 到 Day {d_days} 的所有行程內容！
        絕對不允許使用「以此類推」、「...等」字眼，哪怕文本很長也必須把每一天的每一個行程完整寫完。
        
        請嚴格以下列 JSON 格式回傳，並確保景點座標 (lat, lng) 準確無誤：
        {{
            "summary": "一句話總結這個充滿人情味的行程",
            {hotels_json}
            "itinerary": [
                {{
                    "day": 1,
                    "spots": [
                        {{
                            "name": "景點名稱",
                            "description": "具有溫度的深度參觀心法與景點介紹（不可省略）",
                            "web_intro": "真實旅客的網路評價、鄉民心得或特別推薦亮點（不可省略）",
                            "lat": 緯度數字, "lng": 經度數字, "transit_info": "到下一站的交通建議"
                        }}
                    ]
                }}
            ]
        }}
        """
        try:
            # 升級為 gpt-4o 並拉高 Token 限制確保完整生成
            final_res = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": final_prompt}],
                response_format={"type": "json_object"}, temperature=0.4, max_tokens=8000
            )
            final_data = json.loads(final_res.choices[0].message.content)
            
            st.session_state.travel_result = final_data
            if enable_hotel and verified_hotel_data:
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
            
            encoded_hotel_search = urllib.parse.quote(f"{destination} {hotel_name}")
            google_travel_link = f"https://www.google.com/travel/search?q={encoded_hotel_search}"
            gmaps_hotel_link = f"https://www.google.com/maps/search/?api=1&query={encoded_hotel_search}"
            
            st.info(f"🏨 **核心住宿推薦：{hotel_name}**\n\n{curr_hotel.get('description', '')}")
            
            if st.button("🔄 換一間飯店並重新規劃"):
                build_itinerary(destination, days, travel_date, hotel_budget)

            st.markdown("🚀 **【即時工具直達專區】**")
            st.markdown(f"👉 [點我前往 **Google 旅遊** 查看此飯店比價資訊]({google_travel_link})")
            
            h_pool = st.session_state.link_pools.get(hotel_name, [])
            if h_pool:
                h_idx_key = f"idx_hotel_link_{hotel_name}"
                if h_idx_key not in st.session_state: st.session_state[h_idx_key] = 0
                curr_h_link = h_pool[st.session_state[h_idx_key] % len(h_pool)]
                
                hc1, hc2 = st.columns([4, 1])
                # 這裡改為顯示「真實旅客心得/遊記」
                with hc1: st.markdown(f"👉 [📖 **真實旅客住宿心得與遊記**：{curr_h_link['title']}]({curr_h_link['url']})")
                with hc2:
                    if len(h_pool) > 1 and st.button("🔄 換一篇遊記", key=f"btn_{h_idx_key}"):
                        st.session_state[h_idx_key] += 1
                        st.rerun()

            st.markdown(f"👉 [🌍 在 **Google Maps** 中精確定位與查看評價]({gmaps_hotel_link})")
            st.write("---")
        
        for d_idx, day_info in enumerate(result.get('itinerary', [])):
            day_num = day_info['day']
            if day_num not in allowed_day_numbers: continue
            
            color = DAY_COLORS[d_idx % len(DAY_COLORS)]
            st.markdown(f"### 📅 第 {day_num} 天 (地圖顏色：:{color}[{color.upper()}])")
            
            for s_idx, spot in enumerate(day_info.get('spots', [])):
                if spot.get('transit_info'): st.markdown(f"⬇️ 🚌 *{spot['transit_info']}*")
                    
                with st.expander(f"📍 站點 {s_idx+1}: {spot['name']}"):
                    st.markdown(f"**📝 景點心法**：\n{spot.get('description', '無簡介')}")
                    st.markdown(f"**💬 鄉民/旅客評價**：\n{spot.get('web_intro', '目前暫無評價紀錄')}")
                    st.markdown("---")
                    
                    s_pool = st.session_state.link_pools.get(spot['name'], [])
                    if s_pool:
                        s_idx_key = f"idx_spot_{d_idx}_{s_idx}"
                        if s_idx_key not in st.session_state: st.session_state[s_idx_key] = 0
                        curr_s_link = s_pool[st.session_state[s_idx_key] % len(s_pool)]
                        
                        sc1, sc2 = st.columns([4, 1])
                        # 只留下遊記連結，徹底刪除參考來源 (官方網站)
                        with sc1: st.markdown(f"📖 **[網誌遊記：{curr_s_link['title']}]({curr_s_link['url']})**")
                        with sc2:
                            if len(s_pool) > 1 and st.button("🔄 換一篇", key=f"btn_{s_idx_key}"):
                                st.session_state[s_idx_key] += 1
                                st.rerun()
                        
                    encoded_gmaps_spot = urllib.parse.quote(spot['name'])
                    gmaps_spot_link = f"https://www.google.com/maps/search/?api=1&query={encoded_gmaps_spot}"
                    st.markdown(f"🗺️ **[🌍 在 Google Maps 中開啟此景點]({gmaps_spot_link})**")
            st.write("---")

    with col2:
        st.subheader("🗺️ 旅遊路徑與路網視覺化")
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
        
        map_key = f"travel_map_{st.session_state.hotel_idx}"
        st_folium(m, width="100%", height=600, returned_objects=[], key=map_key)