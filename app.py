import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
from pulp import *
from collections import defaultdict
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import io

st.set_page_config(page_title="İstanbul Trafik Optimizasyonu", layout="wide")

st.title("🚦 İstanbul Trafik Optimizasyonu")
st.markdown("Şirket servis güzergahlarını optimize ederek tepe saatteki trafik yükünü azalt.")

# ── SABİT VERİLER ──
ilceler = {
    "Güngören":      (41.0208, 28.8697),
    "Bakırköy":      (40.9819, 28.8772),
    "Esenler":       (41.0391, 28.8747),
    "Bağcılar":      (41.0391, 28.8378),
    "Gaziosmanpaşa": (41.0654, 28.9123),
    "Eyüpsultan":    (41.0654, 28.9344),
    "Mecidiyeköy":   (41.0694, 28.9947),
    "Beşiktaş":      (41.0430, 29.0069),
    "Kadıköy":       (40.9927, 29.0277),
    "Üsküdar":       (41.0214, 29.0161),
    "Maltepe":       (40.9354, 29.1322),
    "Pendik":        (40.8762, 29.2345),
    "Ümraniye":      (41.0165, 29.1234),
    "Kartal":        (40.9123, 29.1897),
    "Şişli":         (41.0602, 28.9877),
}

mesai_secenekleri = ["07:00","07:30","08:00","08:30","09:00","09:30","10:00"]
mesai_indeks = {s: i for i, s in enumerate(mesai_secenekleri)}

varsayilan_sirketler = pd.DataFrame([
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

# ── SIDEBAR ──
st.sidebar.header("⚙️ Ayarlar")

# Excel yükleme
st.sidebar.subheader("📂 Veri Yükle")
yuklenen = st.sidebar.file_uploader("Şirket Excel dosyası (.xlsx)", type=["xlsx"])

if yuklenen:
    try:
        sirketler = pd.read_excel(yuklenen)
        st.sidebar.success(f"✅ {len(sirketler)} şirket yüklendi!")
        st.sidebar.dataframe(sirketler.head(3), use_container_width=True)
    except Exception as e:
        st.sidebar.error(f"Hata: {e}")
        sirketler = varsayilan_sirketler
else:
    sirketler = varsayilan_sirketler
    st.sidebar.info("Excel yüklenmedi — varsayılan 15 şirket kullanılıyor.")
    with st.sidebar.expander("📋 Excel formatı nasıl olmalı?"):
        st.markdown("""
Şu sütunlar olmalı:
- `isim` — şirket adı
- `lat` — enlem (örn: 41.05)
- `lon` — boylam (örn: 29.02)
- `mevcut_mesai` — örn: 08:00
- `sabit` — True/False
        """)
        ornek = pd.DataFrame([
            {"isim": "Örnek A.Ş.", "lat": 41.05, "lon": 29.02, "mevcut_mesai": "08:00", "sabit": False},
            {"isim": "Örnek Ltd.", "lat": 41.07, "lon": 28.99, "mevcut_mesai": "09:00", "sabit": True},
        ])
        buf = io.BytesIO()
        ornek.to_excel(buf, index=False)
        st.download_button("📥 Örnek Excel indir", buf.getvalue(),
                          "ornek_sirket.xlsx", use_container_width=True)

max_sapma = st.sidebar.slider("Max mesai kayması (adım sayısı)", 1, 4, 2,
                               help="1 adım = 30 dakika")
min_tepe  = st.sidebar.slider("Tepe saatte min. şirket oranı (%)", 5, 40, 15) / 100

# ── GÜZERGAHları simüle et ──
@st.cache_data
def guzergah_olustur(sirket_json, seed=42):
    sirketler_df = pd.read_json(io.StringIO(sirket_json))
    np.random.seed(seed)
    ilce_listesi = list(ilceler.keys())
    guzergahlar = []
    for _, s in sirketler_df.iterrows():
        kac = np.random.randint(3, 6)
        for ilce in np.random.choice(ilce_listesi, kac, replace=False):
            guzergahlar.append({
                "sirket": s["isim"],
                "sirket_lat": s["lat"], "sirket_lon": s["lon"],
                "baslangic_ilce": ilce,
                "baslangic_lat": ilceler[ilce][0],
                "baslangic_lon": ilceler[ilce][1],
                "calisan_sayisi": 20,
            })
    return pd.DataFrame(guzergahlar)

# Basit mesafe hesabı (OSMnx yerine — cloud'da hızlı çalışması için)
def mesafe_km(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    dlat, dlon = radians(lat2-lat1), radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def rota_cakisma_skoru(sirketler_df, mesai_dict):
    """İlçe bazlı çakışma: aynı ilçeden aynı saatte kaç şirket geçiyor?"""
    ilce_saat_yuk = defaultdict(lambda: defaultdict(int))
    guzergahlar = guzergah_olustur(sirketler_df.to_json())
    for _, g in guzergahlar.iterrows():
        saat = mesai_dict.get(g["sirket"], "08:00")
        ilce_saat_yuk[g["baslangic_ilce"]][saat] += g["calisan_sayisi"]
    toplam = sum(yuk for ilce in ilce_saat_yuk.values()
                 for yuk in ilce.values() if yuk > 20)
    return toplam, ilce_saat_yuk

# ── OPTİMİZASYON ──
def optimizasyon_calistir(sirketler_df, max_sapma, min_tepe_oran):
    mesai_dict = dict(zip(sirketler_df["isim"], sirketler_df["mevcut_mesai"]))
    sabit_list = sirketler_df[sirketler_df["sabit"] == True]["isim"].tolist()
    guzergahlar = guzergah_olustur(sirketler_df.to_json())

    def temiz(s):
        return "".join(c if c.isalnum() else "_" for c in s)

    prob = LpProblem("Optimizasyon", LpMinimize)
    x = {(s, saat): LpVariable(f"x_{temiz(s)}_{saat.replace(':','')}", cat="Binary")
         for s in sirketler_df["isim"] for saat in mesai_secenekleri}

    for s in sirketler_df["isim"]:
        prob += lpSum(x[s, saat] for saat in mesai_secenekleri) == 1

    for s in sirketler_df["isim"]:
        mevcut = mesai_indeks.get(mesai_dict.get(s, "08:00"), 2)
        for saat in mesai_secenekleri:
            if abs(mesai_indeks[saat] - mevcut) > max_sapma:
                prob += x[s, saat] == 0

    for s in sabit_list:
        mevcut = mesai_dict.get(s, "08:00")
        if mevcut in mesai_secenekleri:
            prob += x[s, mevcut] == 1

    toplam_cal = guzergahlar["calisan_sayisi"].sum()
    for saat in ["08:00", "08:30", "09:00"]:
        prob += lpSum(
            x[s, saat] * guzergahlar[guzergahlar["sirket"]==s]["calisan_sayisi"].sum()
            for s in sirketler_df["isim"]
        ) >= toplam_cal * min_tepe_oran

    # Hedef: aynı ilçeden aynı saatte giden çalışan sayısını minimize et
    hedef = []
    for ilce in ilceler:
        for saat in mesai_secenekleri:
            ilce_guz = guzergahlar[guzergahlar["baslangic_ilce"] == ilce]
            for _, g in ilce_guz.iterrows():
                hedef.append(x[g["sirket"], saat] * g["calisan_sayisi"])

    prob += lpSum(hedef)
    prob.solve(PULP_CBC_CMD(msg=0))

    yeni = {}
    for s in sirketler_df["isim"]:
        for saat in mesai_secenekleri:
            if value(x[s, saat]) == 1:
                yeni[s] = saat
    return yeni

# ── ANA EKRAN ──
col1, col2 = st.columns([3, 2])

with col2:
    st.subheader("📋 Şirket Listesi")
    st.dataframe(sirketler[["isim","mevcut_mesai","sabit"]], use_container_width=True, hide_index=True)

    if st.button("🚀 Optimizasyonu Çalıştır", use_container_width=True, type="primary"):
        with st.spinner("Hesaplanıyor..."):
            yeni_mesai = optimizasyon_calistir(sirketler, max_sapma, min_tepe)
            st.session_state["yeni_mesai"] = yeni_mesai
            st.session_state["sirketler"] = sirketler

with col1:
    st.subheader("🗺️ Harita")
    m = folium.Map(location=[41.01, 28.96], zoom_start=11, tiles="CartoDB positron")

    renkler_liste = ["#E63946","#2196F3","#4CAF50","#FF9800","#9C27B0",
                     "#00BCD4","#F44336","#3F51B5","#8BC34A","#FF5722",
                     "#607D8B","#E91E63","#009688","#FFC107","#795548"]
    sirket_renk = {s: renkler_liste[i % len(renkler_liste)]
                   for i, s in enumerate(sirketler["isim"])}

    yeni_mesai = st.session_state.get("yeni_mesai", {})

    for _, s in sirketler.iterrows():
        eski = s["mevcut_mesai"]
        yeni = yeni_mesai.get(s["isim"], eski)
        popup_text = f"<b>{s['isim']}</b><br>Eski: {eski}<br>Yeni: {yeni}"
        folium.CircleMarker([s["lat"], s["lon"]], radius=10,
            color=sirket_renk[s["isim"]], fill=True, fill_opacity=0.9,
            popup=folium.Popup(popup_text, max_width=220)
        ).add_to(m)

    guz_df = guzergah_olustur(sirketler.to_json())
    for ilce, (lat, lon) in ilceler.items():
        count = len(guz_df[guz_df["baslangic_ilce"] == ilce])
        folium.CircleMarker([lat, lon], radius=5+count,
            color="gray", fill=True, fill_opacity=0.4,
            popup=f"{ilce} ({count} güzergah)"
        ).add_to(m)

    st_folium(m, height=420, use_container_width=True)

# ── SONUÇLAR ──
if "yeni_mesai" in st.session_state and st.session_state["yeni_mesai"]:
    st.markdown("---")
    st.subheader("📊 Optimizasyon Sonuçları")

    yeni_mesai = st.session_state["yeni_mesai"]
    sirketler  = st.session_state["sirketler"]
    mesai_dict = dict(zip(sirketler["isim"], sirketler["mevcut_mesai"]))

    mevcut_skor, _ = rota_cakisma_skoru(sirketler, mesai_dict)
    yeni_skor, _   = rota_cakisma_skoru(sirketler, yeni_mesai)
    azalma = (mevcut_skor - yeni_skor) / mevcut_skor * 100 if mevcut_skor > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Eski Çakışma Skoru", f"{mevcut_skor:,}")
    m2.metric("Yeni Çakışma Skoru", f"{yeni_skor:,}", f"-{mevcut_skor-yeni_skor:,}")
    m3.metric("Azalma", f"%{azalma:.1f}")
    m4.metric("Kaydırılan Şirket",
              f"{sum(1 for s in sirketler['isim'] if mesai_dict.get(s) != yeni_mesai.get(s))}")

    # Tablo
    sonuc_rows = []
    for s in sirketler["isim"]:
        eski = mesai_dict.get(s, "08:00")
        yeni = yeni_mesai.get(s, eski)
        sonuc_rows.append({
            "Şirket": s,
            "Eski Mesai": eski,
            "Yeni Mesai": yeni,
            "Durum": "✅ Kaydırıldı" if eski != yeni else "— Aynı kaldı"
        })
    sonuc_df = pd.DataFrame(sonuc_rows)
    st.dataframe(sonuc_df, use_container_width=True, hide_index=True)

    # Grafik
    guz_df = guzergah_olustur(sirketler.to_json())
    yuk_e = {s: 0 for s in mesai_secenekleri}
    yuk_y = {s: 0 for s in mesai_secenekleri}
    for _, r in guz_df.iterrows():
        yuk_e[mesai_dict.get(r["sirket"], "08:00")] += r["calisan_sayisi"]
        yuk_y[yeni_mesai.get(r["sirket"], "08:00")]  += r["calisan_sayisi"]

    fig, ax = plt.subplots(figsize=(10, 4))
    xp = range(len(mesai_secenekleri))
    ax.plot(xp, list(yuk_e.values()), "o-", color="#E63946", linewidth=2.5,
            markersize=8, label="Önce")
    ax.plot(xp, list(yuk_y.values()), "o-", color="#4CAF50", linewidth=2.5,
            markersize=8, label="Sonra")
    ax.fill_between(xp, list(yuk_e.values()), list(yuk_y.values()),
                    alpha=0.12, color="#4CAF50")
    ax.set_xticks(list(xp))
    ax.set_xticklabels(mesai_secenekleri)
    ax.set_title("Saate Göre Yol Üzerindeki Çalışan Yükü", fontweight="bold")
    ax.set_ylabel("Çalışan Sayısı")
    ax.legend(); ax.grid(alpha=0.3)
    ax.spines[["top","right"]].set_visible(False)
    st.pyplot(fig)
