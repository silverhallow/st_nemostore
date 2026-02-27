import streamlit as st
import pandas as pd
import sqlite3
import json
import os
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.graph_objects as go
import matplotlib.pyplot as plt
from datetime import datetime

# Page configuration
st.set_page_config(page_title="네모스토어 매물 분석 대시보드", layout="wide")

# Manual Korean font setup for Mac
plt.rcParams['font.family'] = 'AppleGothic'
plt.rcParams['axes.unicode_minus'] = False

# Constants
DB_PATH = "data/nemostore.db"
CURRENT_YEAR = datetime.now().year

# --- 데이터 로드 및 전처리 ---

@st.cache_data
def get_processed_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM stores", conn)
    conn.close()
    
    if df.empty:
        return df

    # JSON 데이터에서 추가 필드 추출 (areaPrice, maintenanceFee 등)
    def extract_from_json(row):
        try:
            data = json.loads(row['raw_json'])
            return pd.Series({
                'area_price_raw': data.get('areaPrice', 0),
                'maint_fee_raw': data.get('maintenanceFee', 0),
                'approval_date': data.get('completionConfirmedDateUtc', 'N/A'),
                'ground_floor_raw': data.get('groundFloor', 0)
            })
        except:
            return pd.Series({'area_price_raw': 0, 'maint_fee_raw': 0, 'approval_date': 'N/A', 'ground_floor_raw': 0})

    extraction = df.apply(extract_from_json, axis=1)
    df = pd.concat([df, extraction], axis=1)

    # 1. 금액 단위 변환 (원 단위 컬럼 생성)
    df['deposit_won'] = df['deposit'] * 10000
    df['monthly_rent_won'] = df['monthlyRent'] * 10000
    df['premium_won'] = df['premium'] * 10000
    df['maintenance_fee_won'] = df['maint_fee_raw'] * 10000
    df['area_price_won_per_m2'] = df['area_price_raw'] * 10000
    
    # 2. 면적 처리
    df['size_py'] = df['size'] / 3.3058
    
    # 3. 파생 변수 생성
    df['total_initial_cost'] = df['deposit_won'] + df['premium_won']
    df['monthly_total_cost'] = df['monthly_rent_won'] + df['maintenance_fee_won']
    
    # 임대 효율성 지표
    df['rent_per_m2'] = df.apply(lambda r: r['monthly_rent_won'] / r['size'] if r['size'] > 0 else 0, axis=1)
    df['rent_per_py'] = df.apply(lambda r: r['monthly_rent_won'] / r['size_py'] if r['size_py'] > 0 else 0, axis=1)
    df['premium_ratio'] = df.apply(lambda r: r['premium_won'] / r['deposit_won'] if r['deposit_won'] > 0 else 0, axis=1)
    
    # 건물 연식 계산 (사용승인일 기준 추출 가능 시 추가)
    def get_age(date_str):
        if date_str == 'N/A' or not date_str: return 0
        try:
            year = int(date_str[:4])
            return CURRENT_YEAR - year
        except:
            return 0
    df['building_age'] = df['approval_date'].apply(get_age)
    
    # 지역구 추출 (주소 정보가 상세하면 더 정확함)
    df['district'] = "강남구" # 샘플 데이터 기준
    
    return df

@st.cache_data
def fetch_and_parse_detail(listing_id):
    url = f"https://www.nemoapp.kr/store/{listing_id}"
    try:
        headers = {"user-agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200: return None
        soup = BeautifulSoup(resp.content, 'html.parser')
        data = {}
        # 상세 파싱 로직 (주소, 시설, 대장 정보 등)
        addr_tag = soup.select_one('p.font-16.text-gray-80')
        data['주소'] = addr_tag.text.strip() if addr_tag else "N/A"
        tables = soup.select('div.detail-table table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                th = row.find('th'); td = row.find('td')
                if th and td: data[th.text.strip()] = td.text.strip()
        facs = soup.select('li.around-facility-content')
        data['주변시설'] = [f.text.strip() for f in facs]
        return data
    except:
        return None

# --- 사이드바 필터 ---

def draw_sidebar(df):
    st.sidebar.title("🔍 필터 설정")
    
    # 업종 필터
    all_cats = sorted(df['businessLargeCodeName'].unique())
    cats = st.sidebar.multiselect("업종 대분류", all_cats, default=all_cats[:3])
    
    # 가격대 슬라이더 (원 단위 변환 기준)
    st.sidebar.markdown("---")
    dep_max = int(df['deposit'].max())
    dep_range = st.sidebar.slider("보증금 범위 (만원)", 0, dep_max, (0, dep_max))
    
    rent_max = int(df['monthlyRent'].max())
    rent_range = st.sidebar.slider("월세 범위 (만원)", 0, rent_max, (0, rent_max))
    
    prem_max = int(df['premium'].max())
    prem_range = st.sidebar.slider("권리금 범위 (만원)", 0, prem_max, (0, prem_max))

    # 면적 슬라이더
    st.sidebar.markdown("---")
    size_max = float(df['size'].max())
    size_range = st.sidebar.slider("전용 면적 (㎡)", 0.0, size_max, (0.0, size_max))

    # 추가 옵션
    is_first_floor = st.sidebar.checkbox("1층 매물만 보기")
    
    # 데이터 필터링 적용
    f_df = df[
        (df['businessLargeCodeName'].isin(cats)) &
        (df['deposit'].between(dep_range[0], dep_range[1])) &
        (df['monthlyRent'].between(rent_range[0], rent_range[1])) &
        (df['premium'].between(prem_range[0], prem_range[1])) &
        (df['size'].between(size_range[0], size_range[1]))
    ]
    
    if is_first_floor:
        f_df = f_df[f_df['floor'] == 1]
        
    return f_df

# --- 페이지 UI ---

def show_kpi_cards(df):
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("총 매물 수", f"{len(df):,} 개")
    k2.metric("평균 보증금", f"{df['deposit_won'].mean():,.0f} 원")
    k3.metric("평균 월세", f"{df['monthly_rent_won'].mean():,.0f} 원")
    k4.metric("평균 권리금", f"{df['premium_won'].mean():,.0f} 원")
    k5.metric("평균 면적", f"{df['size_py'].mean():.1f} 평")

def page_eda(df):
    st.header("📊 전체 시장 분위기 (EDA)")
    show_kpi_cards(df)
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("임대료 및 보증금 분포")
        dist_field = st.selectbox("분포 확인할 지표 선택", ["monthly_rent_won", "deposit_won", "premium_won", "size"])
        fig = px.histogram(df, x=dist_field, nbins=30, title=f"{dist_field} 분포", color_discrete_sequence=['#636EFA'])
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("업종별 평균 비용 비교")
        comp_field = st.selectbox("비교할 비용 지표 선택", ["monthly_rent_won", "premium_won", "total_initial_cost"])
        avg_data = df.groupby('businessLargeCodeName')[comp_field].mean().reset_index().sort_values(by=comp_field)
        fig = px.bar(avg_data, y='businessLargeCodeName', x=comp_field, orientation='h', title=f"업종별 평균 {comp_field}")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("면적 대비 월세 산점도")
    fig = px.scatter(df, x='size', y='monthly_rent_won', color='businessLargeCodeName', 
                     size='premium_won', hover_data=['title'], labels={'size': '면적(㎡)', 'monthly_rent_won': '월세(원)'})
    st.plotly_chart(fig, use_container_width=True)

def page_analysis(df):
    st.header("🏢 업종 및 지역 심층 분석")
    
    st.subheader("업종별 평당 임대료 (효율성 분석)")
    fig = px.box(df, x='businessLargeCodeName', y='rent_per_py', color='businessLargeCodeName', title="업종별 평당 월세 분포")
    st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("보증금 대비 권리금 비율")
        fig = px.violin(df, y='premium_ratio', x='businessLargeCodeName', box=True, points="all", title="보증금 대비 권리금 비중")
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("지역구별 매물 분포")
        dist_counts = df['district'].value_counts().reset_index()
        fig = px.pie(dist_counts, values='count', names='district', hole=0.4, title="지역구별 비중")
        st.plotly_chart(fig, use_container_width=True)

def page_explorer(df):
    st.header("🔎 매물 상세 드릴다운")
    
    search = st.text_input("매물 제목 또는 ID 검색", "")
    if search:
        results = df[df['title'].str.contains(search, case=False) | df['id'].astype(str).str.contains(search)]
    else:
        results = df.head(20)
        
    st.dataframe(results[['listing_number', 'title', 'businessLargeCodeName', 'deposit', 'monthlyRent', 'premium', 'size']], 
                 use_container_width=True, hide_index=True)
    
    st.markdown("---")
    
    if not results.empty:
        selected_title = st.selectbox("상세 정보를 확인할 매물을 선택하세요", results['title'].tolist())
        item = results[results['title'] == selected_title].iloc[0]
        
        st.subheader(f"🏠 {item['title']} 상세")
        
        c1, c2 = st.columns(2)
        with c1:
            st.info("📌 기본 정보")
            st.write(f"- **업종:** {item['businessLargeCodeName']} ({item['businessMiddleCodeName']})")
            st.write(f"- **위치:** {item['nearSubwayStation']}")
            st.write(f"- **면적:** {item['size']:.1f}㎡ (~{item['size_py']:.1f}평)")
            st.write(f"- **층수:** {item['floor']}층 / {item['ground_floor_raw']}층")
            
        with c2:
            st.success("💰 비용 정보")
            st.write(f"- **보증금:** {item['deposit_won']:,} 원")
            st.write(f"- **월세:** {item['monthly_rent_won']:,} 원 (부가세 별도)")
            st.write(f"- **권리금:** {item['premium_won']:,} 원")
            st.write(f"- **관리비:** {item['maintenance_fee_won']:,} 원")
            
            # 초기 비용 계산기
            op_m = st.number_input("예상 초기 운영 개월 수", 1, 12, 3)
            start_cost = item['deposit_won'] + item['premium_won'] + (item['monthly_total_cost'] * op_m)
            st.markdown(f"### 🚩 예상 창업 초기 비용: **{start_cost:,} 원**")

        if st.button("🌐 매물 상세 데이터 연동 (HTML 파싱)"):
            with st.spinner("네모 웹사이트에서 상세 정보를 가져오는 중..."):
                details = fetch_and_parse_detail(item['listing_number'])
                if details:
                    st.markdown("#### 📋 건축물 및 시설 상세 정보")
                    st.table(pd.DataFrame(details.items(), columns=["항목", "내용"]))
                else:
                    st.warning("상세 정보를 불러올 수 없습니다. 원본 사이트 접근을 확인하세요.")

# --- 메인 실행 ---

def main():
    df = get_processed_data()
    if df.empty:
        st.error("데이터베이스를 찾을 수 없거나 데이터가 비어 있습니다. 수집기를 먼저 실행하세요.")
        return
        
    filtered_df = draw_sidebar(df)
    
    tabs = st.tabs(["시장 상황 (EDA)", "심층 분석", "개별 매물 탐색"])
    
    with tabs[0]: page_eda(filtered_df)
    with tabs[1]: page_analysis(filtered_df)
    with tabs[2]: page_explorer(filtered_df)

if __name__ == "__main__":
    main()
