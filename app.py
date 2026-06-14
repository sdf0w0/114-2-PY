import streamlit as st
import folium
from streamlit_folium import st_folium
import os
from dotenv import load_dotenv
from openai import OpenAI
import json
import requests
import datetime
from ddgs import DDGS

# ==================== 防呆路徑修正區 ====================
current_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(current_dir, ".env")
load_dotenv(dotenv_path)

api_key = os.getenv("OPENAI_API_KEY")

st.set_page_config(page_title="智慧旅遊推薦系統 6.0", layout="wide")

st.title("🌍 智慧旅遊推薦系統 6.0")
st.caption("頂配實戰版：動態交通切換 🚌 + 房價預算自訂 💰 + 爬蟲文獻真實溯源 🌐")

if not api_key:
    st.error("❌ 請先在 .env 檔案中設定 OPENAI_API_KEY")
    st.stop()

client = OpenAI(api_key=api_key)
DAY_COLORS = ['red', 'blue', 'green', 'purple', 'orange', 'darkred', 'darkblue']

def get_real_route(start_loc, end_loc):
    url = f"http://router.project-osrm.org/route/v1/driving/{start_loc[1]},{start_loc[0]};{end_loc[1]},{end_loc[0]}?overview=full&geometries=geojson"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data['code'] == 'Ok':
            coords = data['routes'][0]['geometry']['coordinates']
            return [[c[1], c[0]] for c in coords]
    except Exception:
        pass
    return [start_loc, end_loc]

# ==================== 記憶系統初始化 ====================
if "travel_result" not in st.session_state:
    st.session_state.travel_result = None
if "raw_sources" not in st.session_state:
    st.session_state.raw_sources = []

# --- 側邊欄介面設計 ---
with st.sidebar:
    st.header("🎯 旅遊基本偏好")
    destination = st.text_input("想去哪裡玩？", placeholder="例如：東京、台中、花蓮")
    
    # 【新增】旅遊日期選擇
    travel_date = st.date_input("預計旅遊出發日期", datetime.date(2026, 7, 1))
    days = st.slider("旅遊天數", min_value=1, max_value=7, value=3)
    
    # 【新增】交通工具選擇
    st.subheader("🚌 交通方式")
    transit_mode = st.selectbox(
        "請選擇主要交通工具：",
        ["大眾運輸 (捷運/公車/火車/步行)", "自行開車 / 騎車 / 租車"]
    )
    
    # 【新增】核心住宿功能開關與預算篩選
    st.write("---")
    st.header("🏨 住宿設定 (可選項)")
    enable_hotel = st.checkbox("啟用核心飯店 (放射狀行程)", value=True)
    
    hotel_budget = 4000
    hotel_input = ""
    if enable_hotel:
        hotel_budget = st.slider("每晚飯店預算上限 (TWD)", 1000, 15000, 4000, step=500)
        hotel_input = st.text_input("指定住宿飯店 (選填)", placeholder="留空則依預算自動爬取推薦")

    st.write("---")
    st.subheader("景點風格偏好")
    style_nature = st.checkbox("自然風景 🌲", value=True)
    style_culture = st.checkbox("歷史人文 🏯")
    style_food = st.checkbox("美食吃貨 🍜", value=True)
    style_shopping = st.checkbox("逛街購物 🛍️")

    selected_styles = [name for choice, name in zip([style_nature, style_culture, style_food, style_shopping], ["自然風景", "歷史人文", "美食吃貨", "逛街購物"]) if choice]

    submit_button = st.button("開始智能爬蟲與規劃行程 🏃‍♂️", type="primary")
    
    st.write("---")
    st.header("👁️ 視圖控制中心")
    if st.session_state.travel_result is not None:
        available_days = [f"第 {day_info['day']} 天" for day_info in st.session_state.travel_result.get('itinerary', [])]
        selected_display_days = st.multiselect("選擇要顯示的天數路線：", options=available_days, default=available_days)
    else:
        selected_display_days = []

# --- 核心邏輯：動態爬蟲與精準 Prompt ---
if submit_button:
    if destination:
        with st.spinner("🔍 正在啟動網路爬蟲，抓取該地區真實旅遊攻略、飯店當前房價與評價..."):
            try:
                # 根據使用者是否有啟用飯店，組合不同的爬蟲關鍵字
                if enable_hotel:
                    search_query = f"{destination} 旅遊行程 必去景點 {transit_mode} {destination} 飯店 推薦 預算 {hotel_budget}"
                else:
                    search_query = f"{destination} 旅遊行程 必去景點 {transit_mode} 攻略"
                
                search_results = ""
                st.session_state.raw_sources = [] # 清空舊來源
                
                with DDGS() as query_crawler:
                    results = list(query_crawler.text(search_query, max_results=6))
                    for i, r in enumerate(results):
                        title = r.get('title', '未知網頁')
                        href = r.get('href', '#')
                        body = r.get('body', '')
                        search_results += f"【文獻 {i+1}】標題: {title} | 網址: {href}\n內容摘要: {body}\n\n"
                        # 儲存到記憶體中供前端渲染
                        st.session_state.raw_sources.append({"title": title, "url": href, "snippet": body})
                st.info(f"ℹ️ 成功爬取 {len(st.session_state.raw_sources)} 個相關旅遊與房價網站！正交由 AI 進行整合...")
            except Exception as e:
                search_results = f"網路即時爬蟲服務稍忙，將切換為 LLM 內置知識庫。錯誤原因: {e}"
                st.session_state.raw_sources = []

        with st.spinner("🧙‍♂️ 熱情導遊 AI 正在根據交通工具與預算精細規劃中..."):
            try:
                prompt = f"""
                你是一個充滿熱情、幽默且極度嚴謹的台灣在地導遊。
                請針對目的地「{destination}」規劃一個 {days} 天的旅遊行程。
                
                【使用者基本需求】
                - 出發日期：{travel_date.strftime('%Y-%m-%d')}
                - 偏好風格：{', '.join(selected_styles)}
                - 交通工具：{transit_mode} 
                  ※【極重要】如果交通工具是大眾運輸，你的 `transit_info` 和景點介紹中，必須詳細寫出要搭乘哪班公車、哪條捷運線或步行幾分鐘，絕對不可出現『開車經由國道』等字眼！
                
                【核心住宿模式條件設定】
                - 是否啟用飯店核心模式：{enable_hotel}
                - 使用者預算上限：每晚 {hotel_budget} TWD 以內。
                - 指定飯店：{hotel_input if hotel_input else "未指定，請從參考文獻中幫我挑選一間符合預算且交通方便的真實飯店"}
                - 規則：如果啟用飯店模式，每天行程必須從該飯店出發，最後回到該飯店。如果未啟用，則 `hotel` 欄位請回傳 null。
                
                【撰寫風格與資料來源標註】
                1. 語氣要生動活潑、人性化，帶有開心的情緒（多用驚嘆號或適度顏文字），像個專業的旅遊部落客！
                2. 介紹必須詳盡、有血有肉（至少 3-4 句話），點出景點好玩、好吃、好拍在哪裡。
                3. 【文獻引用規範】：檢查下面的「真實網路爬蟲資訊」，如果某個景點或飯店的資訊是從文獻中擷取出來的，請務必在該景點的 `description` 結尾加上『資料來源：[文獻標題](網址)』。如果是你憑藉內置知識庫推薦的，請寫『資料來源：AI 導遊生成』。
                
                真實網路爬蟲資訊：
                {search_results}
                
                請嚴格以下列 JSON 格式回傳，不要包含額外的 Markdown 標籤：
                {{
                    "summary": "一句話總結這個行程的精彩亮點",
                    "hotel": {{
                        "name": "飯店名稱", 
                        "description": "充滿熱情的飯店介紹，並說明目前的預估房價（必須符合預算）與交通便利性，最後一定要附上資料來源標註。", 
                        "lat": 緯度數字, 
                        "lng": 經度數字
                    }},
                    "itinerary": [
                        {{
                            "day": 1,
                            "spots": [
                                {{
                                    "name": "景點名稱", 
                                    "description": "生動活潑的詳細景點介紹，融合必吃必玩重點，並在尾端強制換行標註資料來源（來源網址必須與爬蟲資訊相符）。", 
                                    "lat": 緯度數字, 
                                    "lng": 經度數字,
                                    "transit_info": "配合交通工具設定，詳細描述如何前往下一站（大眾運輸請寫車次或步行時間）"
                                }}
                            ]
                        }}
                    ]
                }}
                """
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.6 
                )
                st.session_state.travel_result = json.loads(response.choices[0].message.content)
                st.rerun()
            except Exception as e:
                st.error(f"AI 生成發生錯誤：{e}")
    else:
        st.warning("⚠️ 請先輸入你想去的目的地！")

# --- 畫面渲染區 ---
if st.session_state.raw_sources:
    with st.expander("🌐 🔍 檢視本次行程規劃：網路爬蟲即時抓取之真實參考網頁資料庫"):
        st.write("系統已成功將以下真實網頁資料擷取並餵給 AI 進行自檢與過濾：")
        for src in st.session_state.raw_sources:
            st.markdown(f"* **[{src['title']}]({src['url']})**")
            st.caption(f"內容摘要：{src['snippet'][:120]}...")

if st.session_state.travel_result is not None:
    result = st.session_state.travel_result
    col1, col2 = st.columns([1, 1])
    allowed_day_numbers = [int(s.split(" ")[1]) for s in selected_display_days] if selected_display_days else []
    
    # 精準判斷是否有啟用且成功生成飯店
    has_hotel = enable_hotel and "hotel" in result and result["hotel"] is not None
    
    with col1:
        st.success(f"✨ **導遊真心話：** {result.get('summary', '無摘要')}")
        
        if has_hotel:
            st.info(f"🏨 **今晚下榻的核心基地：{result['hotel']['name']}**\n\n{result['hotel'].get('description', '')}")
        else:
            st.warning("🏃‍♂️ **自由逐浪模式：** 本次行程未啟用核心住宿，每天將沿著景點路網一路玩下去！")
        
        for d_idx, day_info in enumerate(result.get('itinerary', [])):
            day_num = day_info['day']
            if day_num not in allowed_day_numbers: continue
            color = DAY_COLORS[d_idx % len(DAY_COLORS)]
            
            st.markdown(f"### 📅 第 {day_num} 天 (地圖顏色：:{color}[{color.upper()}])")
            if has_hotel:
                st.markdown(f"🏠 *活力滿滿！從 **{result['hotel']['name']}** 出發囉！*")
            
            for s_idx, spot in enumerate(day_info.get('spots', [])):
                if spot.get('transit_info'):
                    st.markdown(f"⬇️ 🚌 *{spot['transit_info']}*")
                    
                with st.expander(f"📍 站點 {s_idx+1}: {spot['name']}"):
                    st.markdown(spot.get('description', '無簡介'))
                    st.caption(f"座標定位：{spot['lat']}, {spot['lng']}")
            
            if has_hotel:
                st.markdown(f"⬇️ 🚗 *逛累囉！買點夜市小吃，返回 **{result['hotel']['name']}** 休息*")
            st.write("---")

    with col2:
        st.subheader("🗺️ 旅遊路徑與路網視覺化")
        
        # 決定地圖初始中心點
        if has_hotel:
            base_loc = [result['hotel']['lat'], result['hotel']['lng']]
        else:
            try:
                first_spot = result['itinerary'][0]['spots'][0]
                base_loc = [first_spot['lat'], first_spot['lng']]
            except:
                base_loc = [23.5, 121.0]
                
        m = folium.Map(location=base_loc, zoom_start=12)
        
        if has_hotel:
            folium.Marker(
                location=base_loc,
                popup=f"🏨 住宿核心：{result['hotel']['name']}",
                tooltip=folium.Tooltip("🏠 核心飯店", permanent=True),
                icon=folium.Icon(color="black", icon="home")
            ).add_to(m)
        
        for d_idx, day_info in enumerate(result.get('itinerary', [])):
            day_num = day_info['day']
            if day_num not in allowed_day_numbers: continue
            color = DAY_COLORS[d_idx % len(DAY_COLORS)]
            spots = day_info.get('spots', [])
            
            if has_hotel:
                day_route_points = [base_loc] + [[s['lat'], s['lng']] for s in spots] + [base_loc]
            else:
                day_route_points = [[s['lat'], s['lng']] for s in spots]
            
            for s_idx, spot in enumerate(spots):
                loc = [spot['lat'], spot['lng']]
                folium.Marker(
                    location=loc,
                    popup=f"第 {day_num} 天 - {spot['name']}",
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
                        locations=route_coords,
                        color=color,
                        weight=4,
                        opacity=0.8,
                        dash_array='10, 10' if is_to_from_hotel else None 
                    ).add_to(m)
        
        st_folium(m, width="100%", height=600, returned_objects=[], key="v6.0_travel_map")