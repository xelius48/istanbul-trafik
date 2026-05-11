import streamlit as st
import pandas as pd
import numpy as np
import folium
import requests as req
from streamlit_folium import st_folium
from pulp import *
from collections import defaultdict
import matplotlib.pyplot as plt

st.set_page_config(page_title="İstanbul Trafik Optimizasyonu", layout="wide")
st.title("🚦 İstanbul Trafik Optimizasyonu")
st.markdown("Şirket servis güzergahlarını optimize ederek tepe saatteki trafik yükünü azalt.")

mesai_secenekleri = ["07:00","07:30","08:00","08:30","09:00","09:30","10:00"]
mesai_indeks      = {s: i for i, s in enumerate(mesai_secenekleri)}

# Saate göre trafik yoğunluk katsayısı
trafik_katsayi = {
    "07:00": 1.3, "07:30": 1.5, "08:00": 1.8,
    "08:30": 1.9, "09:00": 1.7, "09:30": 1.3, "10:00": 1.1
}

RENKLER = ["#E63946","#2196F3","#4CAF50","#FF9800","#9C27B0",
           "#00BCD4","#F44336","#3F51B5","#8BC34A","#FF5722",
           "#607D8B","#E91E63","#009688","#FFC107","#795548"]

# ── VERİ TEMİZLEME ──
def df_temizle_sirket(df):
    d = df.copy()
    d["lat"]          = d["lat"].astype(float)
    d["lon"]          = d["lon"].astype(float)
    d["isim"]         = d["isim"].astype(str)
    d["mevcut_mesai"] = d["mevcut_mesai"].astype(str)
    d["sabit"]        = d["sabit"].astype(bool)
    return d

def df_temizle_guzergah(df):
    d = df.copy()
    d["sirket"]         = d["sirket"].astype(str)
    d["baslangic_ilce"] = d["baslangic_ilce"].astype(str)
    d["baslangic_lat"]  = d["baslangic_lat"].astype(float)
    d["baslangic_lon"]  = d["baslangic_lon"].astype(float)
    d["calisan_sayisi"] = d["calisan_sayisi"].astype(int)
    return d

def guzergaha_sirket_konum_ekle(guzergah_df, sirket_df):
    konum = sirket_df.set_index("isim")[["lat","lon"]].rename(
        columns={"lat":"sirket_lat","lon":"sirket_lon"})
    d = guzergah_df.join(konum, on="sirket", how="left")
    d["sirket_lat"] = d["sirket_lat"].fillna(41.0).astype(float)
    d["sirket_lon"] = d["sirket_lon"].fillna(29.0).astype(float)
    return d

# ── OSRM ROTA ──
@st.cache_data(show_spinner=False)
def gercek_rota(lat1, lon1, lat2, lon2):
    try:
        url = (f"http://router.project-osrm.org/route/v1/driving/"
               f"{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson")
        r = req.get(url, timeout=5)
        rota   = r.json()["routes"][0]
        coords = rota["geometry"]["coordinates"]
        sure   = float(rota["duration"])
        return [[c[1], c[0]] for c in coords], sure
    except:
        return [[lat1, lon1], [lat2, lon2]], 1800.0

# ── SÜRE HESABI ──
def sure_hesapla(guzergah_df, mesai_dict):
    toplam_sure = 0.0
    toplam_kisi = 0
    detay = []
    for _, g in guzergah_df.iterrows():
        sirket  = str(g["sirket"])
        saat    = mesai_dict.get(sirket, "08:00")
        katsayi = trafik_katsayi.get(saat, 1.5)
        _, sure_sn = gercek_rota(
            float(g["baslangic_lat"]), float(g["baslangic_lon"]),
            float(g["sirket_lat"]),    float(g["sirket_lon"])
        )
        gercek_sure = (sure_sn * katsayi) / 60.0
        kisi = int(g["calisan_sayisi"])
        toplam_sure += gercek_sure * kisi
        toplam_kisi += kisi
        detay.append({
            "Şirket":            sirket,
            "İlçe":              str(g["baslangic_ilce"]),
            "Mesai":             saat,
            "Tahmini Süre (dk)": round(gercek_sure, 1),
            "Çalışan":           kisi
        })
    ort = toplam_sure / toplam_kisi if toplam_kisi > 0 else 0.0
    return round(ort, 1), pd.DataFrame(detay)

# ── ÇAKIŞMA HESABI ──
def cakisma_hesapla(guzergah_df, mesai_dict):
    ilce_saat = defaultdict(lambda: defaultdict(int))
    for _, g in guzergah_df.iterrows():
        saat = mesai_dict.get(str(g["sirket"]), "08:00")
        ilce_saat[str(g["baslangic_ilce"])][saat] += int(g["calisan_sayisi"])
    toplam, detay = 0, []
    for ilce, saatler in ilce_saat.items():
        for saat, yuk in saatler.items():
            if yuk > 20:
                toplam += yuk
                detay.append({"İlçe": ilce, "Saat": saat, "Yük": yuk})
    detay_df = pd.DataFrame(detay).sort_values("Yük", ascending=False) if detay else pd.DataFrame()
    return toplam, detay_df

# ── OPTİMİZASYON ──
def optimizasyon_calistir(sirketler_df, guzergah_df, max_sapma, min_tepe_oran):
    mesai_dict = {str(r["isim"]): str(r["mevcut_mesai"]) for _, r in sirketler_df.iterrows()}
    sabit_list = [str(r["isim"]) for _, r in sirketler_df.iterrows() if r["sabit"]]
    isimler    = [str(r["isim"]) for _, r in sirketler_df.iterrows()]

    def temiz(s):
        return "".join(c if c.isalnum() else "_" for c in str(s))

    prob = LpProblem("Optimizasyon", LpMinimize)
    x = {(s, saat): LpVariable(f"x_{temiz(s)}_{saat.replace(':','')}", cat="Binary")
         for s in isimler for saat in mesai_secenekleri}

    for s in isimler:
        prob += lpSum(x[s, saat] for saat in mesai_secenekleri) == 1

    for s in isimler:
        mevcut = mesai_indeks.get(mesai_dict.get(s, "08:00"), 2)
        for saat in mesai_secenekleri:
            if abs(mesai_indeks[saat] - mevcut) > max_sapma:
                prob += x[s, saat] == 0

    for s in sabit_list:
        m = mesai_dict.get(s, "08:00")
        if m in mesai_secenekleri:
            prob += x[s, m] == 1

    toplam_cal = int(guzergah_df["calisan_sayisi"].sum())
    for tepe_saat in ["08:00","08:30","09:00"]:
        prob += lpSum(
            x[s, tepe_saat] * int(guzergah_df[guzergah_df["sirket"]==s]["calisan_sayisi"].sum())
            for s in isimler
        ) >= toplam_cal * min_tepe_oran

    hedef = []
    for ilce in guzergah_df["baslangic_ilce"].unique():
        ilce_guz = guzergah_df[guzergah_df["baslangic_ilce"] == ilce]
        for saat in mesai_secenekleri:
            for _, g in ilce_guz.iterrows():
                if str(g["sirket"]) in isimler:
                    hedef.append(x[str(g["sirket"]), saat] * int(g["calisan_sayisi"]))

    prob += lpSum(hedef) if hedef else 0
    prob.solve(PULP_CBC_CMD(msg=0))

    yeni = {}
    for s in isimler:
        for saat in mesai_secenekleri:
            if value(x[s, saat]) == 1:
                yeni[s] = saat
    return yeni

# ── VARSAYILAN VERİ ──
VARSAYILAN_SIRKETLER = pd.DataFrame([
    {"isim": "TechPark A.Ş.",   "lat": 41.0500, "lon": 29.0200, "mevcut_mesai": "08:00", "sabit": False},
    {"isim": "Finans Bank",      "lat": 41.0694, "lon": 29.0104, "mevcut_mesai": "09:00", "sabit": False},
    {"isim": "Medya Grubu",      "lat": 41.0482, "lon": 28.9912, "mevcut_mesai": "09:00", "sabit": False},
    {"isim": "Yazılım Ltd.",     "lat": 41.0321, "lon": 29.0050, "mevcut_mesai": "08:00", "sabit": False},
    {"isim": "Sigorta A.Ş.",     "lat": 41.0234, "lon": 28.9543, "mevcut_mesai": "08:30", "sabit": False},
    {"isim": "Lojistik Ltd.",    "lat": 41.0391, "lon": 28.8378, "mevcut_mesai": "07:30", "sabit": True},
    {"isim": "Üretim A.Ş.",      "lat": 40.9927, "lon": 29.0277, "mevcut_mesai": "08:00", "sabit": False},
    {"isim": "Danışmanlık Ltd.", "lat": 41.0462, "lon": 29.0338, "mevcut_mesai": "09:30", "sabit": False},
    {"isim": "E-Ticaret A.Ş.",   "lat": 41.0123, "lon": 28.9234, "mevcut_mesai": "08:00", "sabit": False},
    {"isim": "Sağlık Grubu",     "lat": 40.9821, "lon": 29.0123, "mevcut_mesai": "08:30", "sabit": True},
    {"isim": "Eğitim Ltd.",      "lat": 41.0654, "lon": 28.8976, "mevcut_mesai": "08:00", "sabit": False},
    {"isim": "Enerji A.Ş.",      "lat": 41.0321, "lon": 28.8654, "mevcut_mesai": "07:00", "sabit": False},
    {"isim": "Perakende Ltd.",   "lat": 40.9654, "lon": 28.7923, "mevcut_mesai": "09:00", "sabit": False},
    {"isim": "Turizm A.Ş.",      "lat": 41.0987, "lon": 28.9432, "mevcut_mesai": "10:00", "sabit": False},
    {"isim": "İnşaat Grubu",     "lat": 41.0543, "lon": 28.9876, "mevcut_mesai": "07:30", "sabit": True},
])

VARSAYILAN_GUZERGAHLAR = pd.DataFrame([
    ("TechPark A.Ş.",   "Kadıköy",       40.9927, 29.0277, 20),
    ("TechPark A.Ş.",   "Üsküdar",       41.0214, 29.0161, 20),
    ("TechPark A.Ş.",   "Pendik",        40.8762, 29.2345, 20),
    ("TechPark A.Ş.",   "Ümraniye",      41.0165, 29.1234, 20),
    ("Finans Bank",      "Güngören",      41.0208, 28.8697, 20),
    ("Finans Bank",      "Bakırköy",      40.9819, 28.8772, 20),
    ("Finans Bank",      "Esenler",       41.0391, 28.8747, 20),
    ("Medya Grubu",      "Şişli",         41.0602, 28.9877, 20),
    ("Medya Grubu",      "Beşiktaş",      41.0430, 29.0069, 20),
    ("Medya Grubu",      "Mecidiyeköy",   41.0694, 28.9947, 20),
    ("Yazılım Ltd.",     "Maltepe",       40.9354, 29.1322, 20),
    ("Yazılım Ltd.",     "Kartal",        40.9123, 29.1897, 20),
    ("Yazılım Ltd.",     "Kadıköy",       40.9927, 29.0277, 20),
    ("Sigorta A.Ş.",     "Beşiktaş",      41.0430, 29.0069, 20),
    ("Sigorta A.Ş.",     "Şişli",         41.0602, 28.9877, 20),
    ("Sigorta A.Ş.",     "Eyüpsultan",    41.0654, 28.9344, 20),
    ("Lojistik Ltd.",    "Bağcılar",      41.0391, 28.8378, 20),
    ("Lojistik Ltd.",    "Esenler",       41.0391, 28.8747, 20),
    ("Lojistik Ltd.",    "Güngören",      41.0208, 28.8697, 20),
    ("Üretim A.Ş.",      "Gaziosmanpaşa", 41.0654, 28.9123, 20),
    ("Üretim A.Ş.",      "Eyüpsultan",    41.0654, 28.9344, 20),
    ("Üretim A.Ş.",      "Üsküdar",       41.0214, 29.0161, 20),
    ("Danışmanlık Ltd.", "Kadıköy",       40.9927, 29.0277, 20),
    ("Danışmanlık Ltd.", "Maltepe",       40.9354, 29.1322, 20),
    ("E-Ticaret A.Ş.",   "Bakırköy",      40.9819, 28.8772, 20),
    ("E-Ticaret A.Ş.",   "Güngören",      41.0208, 28.8697, 20),
    ("E-Ticaret A.Ş.",   "Bağcılar",      41.0391, 28.8378, 20),
    ("Sağlık Grubu",     "Ümraniye",      41.0165, 29.1234, 20),
    ("Sağlık Grubu",     "Pendik",        40.8762, 29.2345, 20),
    ("Sağlık Grubu",     "Kartal",        40.9123, 29.1897, 20),
    ("Eğitim Ltd.",      "Esenler",       41.0391, 28.8747, 20),
    ("Eğitim Ltd.",      "Bağcılar",      41.0391, 28.8378, 20),
    ("Enerji A.Ş.",      "Güngören",      41.0208, 28.8697, 20),
    ("Enerji A.Ş.",      "Bakırköy",      40.9819, 28.8772, 20),
    ("Perakende Ltd.",   "Bakırköy",      40.9819, 28.8772, 20),
    ("Perakende Ltd.",   "Güngören",      41.0208, 28.8697, 20),
    ("Perakende Ltd.",   "Esenler",       41.0391, 28.8747, 20),
    ("Turizm A.Ş.",      "Eyüpsultan",    41.0654, 28.9344, 20),
    ("Turizm A.Ş.",      "Gaziosmanpaşa", 41.0654, 28.9123, 20),
    ("İnşaat Grubu",     "Mecidiyeköy",   41.0694, 28.9947, 20),
    ("İnşaat Grubu",     "Şişli",         41.0602, 28.9877, 20),
], columns=["sirket","baslangic_ilce","baslangic_lat","baslangic_lon","calisan_sayisi"])

# ── SIDEBAR ──
st.sidebar.header("⚙️ Ayarlar")
st.sidebar.subheader("📂 Excel Veri Yükle")
yuklenen = st.sidebar.file_uploader("Excel dosyası (.xlsx)", type=["xlsx"])

sirketler   = df_temizle_sirket(VARSAYILAN_SIRKETLER)
guzergahlar = df_temizle_guzergah(VARSAYILAN_GUZERGAHLAR)

if yuklenen:
    try:
        xl = pd.ExcelFile(yuklenen)
        if "sirketler" in xl.sheet_names and "guzergahlar" in xl.sheet_names:
            sirketler   = df_temizle_sirket(xl.parse("sirketler"))
            guzergahlar = df_temizle_guzergah(xl.parse("guzergahlar"))
            st.sidebar.success(f"✅ {len(sirketler)} şirket, {len(guzergahlar)} güzergah yüklendi!")
        else:
            st.sidebar.error("❌ 'sirketler' ve 'guzergahlar' sayfaları gerekli!")
    except Exception as e:
        st.sidebar.error(f"Hata: {e}")
else:
    st.sidebar.info("Varsayılan 15 şirket kullanılıyor.")

with st.sidebar.expander("📋 Excel formatı"):
    st.markdown("""
**sirketler:** isim, lat, lon, mevcut_mesai, sabit

**guzergahlar:** sirket, baslangic_ilce, baslangic_lat, baslangic_lon, calisan_sayisi
    """)

max_sapma = st.sidebar.slider("Max mesai kayması (adım)", 1, 4, 2, help="1 adım = 30 dk")
min_tepe  = st.sidebar.slider("Tepe saatte min. oran (%)", 5, 40, 15) / 100

# Güzergaha şirket konumu ekle
guzergahlar = guzergaha_sirket_konum_ekle(guzergahlar, sirketler)
sirket_renk = {str(r["isim"]): RENKLER[i % len(RENKLER)]
               for i, (_, r) in enumerate(sirketler.iterrows())}

# ── ANA EKRAN ──
col1, col2 = st.columns([3, 2])

with col2:
    st.subheader("📋 Şirketler")
    st.dataframe(sirketler[["isim","mevcut_mesai","sabit"]], use_container_width=True, hide_index=True)

    ozet = guzergahlar.groupby("sirket").agg(
        guzergah=("baslangic_ilce","count"),
        calisan=("calisan_sayisi","sum")
    ).reset_index()
    st.subheader("🚌 Güzergah Özeti")
    st.dataframe(ozet, use_container_width=True, hide_index=True)

    if st.button("🚀 Optimizasyonu Çalıştır", use_container_width=True, type="primary"):
        with st.spinner("Hesaplanıyor..."):
            yeni_mesai = optimizasyon_calistir(sirketler, guzergahlar, max_sapma, min_tepe)
            st.session_state["yeni_mesai"]  = yeni_mesai
            st.session_state["sirketler"]   = sirketler
            st.session_state["guzergahlar"] = guzergahlar

with col1:
    st.subheader("🗺️ Harita")
    yeni_mesai = st.session_state.get("yeni_mesai", {})

    m = folium.Map(location=[41.01, 28.96], zoom_start=11, tiles="CartoDB positron")

    # Güzergah çizgileri
    for _, g in guzergahlar.iterrows():
        sirket_adi = str(g["sirket"])
        if sirket_adi in sirketler["isim"].values:
            s_row = sirketler[sirketler["isim"] == sirket_adi].iloc[0]
            koordinatlar, _ = gercek_rota(
                float(g["baslangic_lat"]), float(g["baslangic_lon"]),
                float(s_row["lat"]),       float(s_row["lon"])
            )
            folium.PolyLine(
                locations=koordinatlar,
                color=sirket_renk.get(sirket_adi, "gray"),
                weight=2, opacity=0.5,
                tooltip=f"{sirket_adi} | {str(g['baslangic_ilce'])} ({int(g['calisan_sayisi'])} kişi)"
            ).add_to(m)

    # İlçe noktaları
    ilce_grp = guzergahlar.groupby("baslangic_ilce").agg(
        lat=("baslangic_lat","first"),
        lon=("baslangic_lon","first"),
        toplam=("calisan_sayisi","sum")
    ).reset_index()
    for _, r in ilce_grp.iterrows():
        folium.CircleMarker(
            location=[float(r["lat"]), float(r["lon"])],
            radius=int(5 + min(int(r["toplam"]) // 20, 8)),
            color="gray", fill=True, fill_opacity=0.5,
            tooltip=f"{str(r['baslangic_ilce'])} — {int(r['toplam'])} çalışan"
        ).add_to(m)

    # Şirket noktaları
    for _, s in sirketler.iterrows():
        isim  = str(s["isim"])
        eski  = str(s["mevcut_mesai"])
        yeni  = yeni_mesai.get(isim, eski)
        degisti = eski != yeni
        folium.CircleMarker(
            location=[float(s["lat"]), float(s["lon"])],
            radius=12,
            color=sirket_renk.get(isim, "#333"),
            fill=True, fill_opacity=0.9,
            popup=folium.Popup(
                f"<b>{isim}</b><br>Eski: {eski}<br>Yeni: {yeni}<br>"
                f"{'✅ Kaydırıldı' if degisti else '— Değişmedi'}",
                max_width=220)
        ).add_to(m)

    st_folium(m, height=450, use_container_width=True)

# ── SONUÇLAR ──
if "yeni_mesai" in st.session_state and st.session_state["yeni_mesai"]:
    st.markdown("---")
    st.subheader("📊 Optimizasyon Sonuçları")

    yeni_mesai  = st.session_state["yeni_mesai"]
    sirketler_s = df_temizle_sirket(st.session_state["sirketler"])
    guzergah_s  = df_temizle_guzergah(st.session_state["guzergahlar"])
    guzergah_s  = guzergaha_sirket_konum_ekle(guzergah_s, sirketler_s)
    mesai_dict  = {str(r["isim"]): str(r["mevcut_mesai"]) for _, r in sirketler_s.iterrows()}

    mevcut_skor, _ = cakisma_hesapla(guzergah_s, mesai_dict)
    yeni_skor,   _ = cakisma_hesapla(guzergah_s, yeni_mesai)
    azalma         = (mevcut_skor - yeni_skor) / mevcut_skor * 100 if mevcut_skor > 0 else 0
    kaydirilan     = sum(1 for s in sirketler_s["isim"] if mesai_dict.get(str(s)) != yeni_mesai.get(str(s)))

    ort_sure_eski, detay_eski = sure_hesapla(guzergah_s, mesai_dict)
    ort_sure_yeni, detay_yeni = sure_hesapla(guzergah_s, yeni_mesai)
    sure_fark  = ort_sure_eski - ort_sure_yeni

    # Metrikler
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Eski Çakışma",       f"{mevcut_skor:,}")
    m2.metric("Yeni Çakışma",       f"{yeni_skor:,}",      f"-{mevcut_skor-yeni_skor:,}")
    m3.metric("Çakışma Azalması",   f"%{azalma:.1f}")
    m4.metric("Kaydırılan Şirket",  f"{kaydirilan}/{len(sirketler_s)}")
    m5.metric("Eski Ort. Süre",     f"{ort_sure_eski} dk")
    m6.metric("Yeni Ort. Süre",     f"{ort_sure_yeni} dk", f"-{sure_fark:.1f} dk")

    # Sonuç tablosu
    sonuc_rows = []
    for _, s in sirketler_s.iterrows():
        isim = str(s["isim"])
        eski = mesai_dict.get(isim, "08:00")
        yeni = yeni_mesai.get(isim, eski)
        guz  = guzergah_s[guzergah_s["sirket"] == isim]
        sonuc_rows.append({
            "Şirket":    isim,
            "Eski":      eski,
            "Yeni":      yeni,
            "Güzergah":  len(guz),
            "Çalışan":   int(guz["calisan_sayisi"].sum()),
            "Durum":     "✅ Kaydırıldı" if eski != yeni else "— Aynı"
        })
    st.dataframe(pd.DataFrame(sonuc_rows), use_container_width=True, hide_index=True)

    # Süre detayı
    with st.expander("🕐 Güzergah Bazlı Süre Detayı"):
        cx, cy = st.columns(2)
        with cx:
            st.markdown("**Önce (mevcut mesai):**")
            st.dataframe(detay_eski.sort_values("Tahmini Süre (dk)", ascending=False),
                         use_container_width=True, hide_index=True)
        with cy:
            st.markdown("**Sonra (optimize):**")
            st.dataframe(detay_yeni.sort_values("Tahmini Süre (dk)", ascending=False),
                         use_container_width=True, hide_index=True)

    # Grafik
    yuk_e = {s: 0 for s in mesai_secenekleri}
    yuk_y = {s: 0 for s in mesai_secenekleri}
    for _, g in guzergah_s.iterrows():
        s = str(g["sirket"])
        e = mesai_dict.get(s, "08:00")
        y = yeni_mesai.get(s, e)
        if e in yuk_e: yuk_e[e] += int(g["calisan_sayisi"])
        if y in yuk_y: yuk_y[y] += int(g["calisan_sayisi"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4))
    xp = list(range(len(mesai_secenekleri)))

    ax1.plot(xp, list(yuk_e.values()), "o-", color="#E63946", lw=2.5, ms=8, label="Önce")
    ax1.plot(xp, list(yuk_y.values()), "o-", color="#4CAF50", lw=2.5, ms=8, label="Sonra")
    ax1.fill_between(xp, list(yuk_e.values()), list(yuk_y.values()), alpha=0.12, color="#4CAF50")
    ax1.set_xticks(xp); ax1.set_xticklabels(mesai_secenekleri, rotation=30)
    ax1.set_title("Saate Göre Çalışan Yükü", fontweight="bold")
    ax1.set_ylabel("Çalışan Sayısı")
    ax1.legend(); ax1.grid(alpha=0.3); ax1.spines[["top","right"]].set_visible(False)

    ax2.bar(["Önce","Sonra"], [ort_sure_eski, ort_sure_yeni],
            color=["#E63946","#4CAF50"], width=0.5, edgecolor="white")
    for i, v in enumerate([ort_sure_eski, ort_sure_yeni]):
        ax2.text(i, v + 0.3, f"{v} dk", ha="center", fontweight="bold")
    ax2.set_title("Ortalama İşe Gidiş Süresi", fontweight="bold")
    ax2.set_ylabel("Dakika")
    ax2.set_ylim(0, max(ort_sure_eski, ort_sure_yeni) * 1.3)
    ax2.spines[["top","right"]].set_visible(False)

    st.pyplot(fig)
