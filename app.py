import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
from pulp import *
from collections import defaultdict
import matplotlib.pyplot as plt
import io

st.set_page_config(page_title="İstanbul Trafik Optimizasyonu", layout="wide")
st.title("🚦 İstanbul Trafik Optimizasyonu")
st.markdown("Şirket servis güzergahlarını optimize ederek tepe saatteki trafik yükünü azalt.")

# ── SABİT ──
mesai_secenekleri = ["07:00","07:30","08:00","08:30","09:00","09:30","10:00"]
mesai_indeks = {s: i for i, s in enumerate(mesai_secenekleri)}

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

sirketler   = VARSAYILAN_SIRKETLER.copy()
guzergahlar = VARSAYILAN_GUZERGAHLAR.copy()

if yuklenen:
    try:
        xl = pd.ExcelFile(yuklenen)
        
        if "sirketler" in xl.sheet_names and "guzergahlar" in xl.sheet_names:
            sirketler   = xl.parse("sirketler")
            guzergahlar = xl.parse("guzergahlar")
            
            # Sabit sütunu boolean'a çevir
            if "sabit" in sirketler.columns:
                sirketler["sabit"] = sirketler["sabit"].astype(bool)
            else:
                sirketler["sabit"] = False

            st.sidebar.success(f"✅ {len(sirketler)} şirket, {len(guzergahlar)} güzergah yüklendi!")
        else:
            st.sidebar.error("❌ Excel'de 'sirketler' ve 'guzergahlar' sayfaları olmalı!")
    except Exception as e:
        st.sidebar.error(f"Hata: {e}")
else:
    st.sidebar.info("Excel yüklenmedi — varsayılan veri kullanılıyor.")

# Örnek Excel indirme
with st.sidebar.expander("📋 Excel formatı nasıl olmalı?"):
    st.markdown("""
**2 sayfa gerekli:**

**sirketler sayfası:**
- `isim` — şirket adı
- `lat` — enlem (örn: 41.05)
- `lon` — boylam (örn: 29.02)
- `mevcut_mesai` — örn: 08:00
- `sabit` — TRUE/FALSE

**guzergahlar sayfası:**
- `sirket` — şirket adı (sirketler ile aynı)
- `baslangic_ilce` — ilçe adı
- `baslangic_lat` — ilçe enlemi
- `baslangic_lon` — ilçe boylamı
- `calisan_sayisi` — o ilçeden gelen çalışan
    """)

max_sapma = st.sidebar.slider("Max mesai kayması", 1, 4, 2, help="1 = 30 dakika")
min_tepe  = st.sidebar.slider("Tepe saatte min. şirket oranı (%)", 5, 40, 15) / 100

# ── ÇAKIŞMA HESABI ──
def cakisma_hesapla(sirketler_df, guzergah_df, mesai_dict):
    ilce_saat = defaultdict(lambda: defaultdict(int))
    for _, g in guzergah_df.iterrows():
        saat = mesai_dict.get(g["sirket"], "08:00")
        ilce_saat[g["baslangic_ilce"]][saat] += int(g["calisan_sayisi"])
    
    toplam = 0
    detay  = []
    for ilce, saatler in ilce_saat.items():
        for saat, yuk in saatler.items():
            if yuk > 20:
                toplam += yuk
                detay.append({"İlçe": ilce, "Saat": saat, "Yük": yuk})
    
    return toplam, pd.DataFrame(detay).sort_values("Yük", ascending=False) if detay else pd.DataFrame()

# ── OPTİMİZASYON ──
def optimizasyon_calistir(sirketler_df, guzergah_df, max_sapma, min_tepe_oran):
    mesai_dict = dict(zip(sirketler_df["isim"], sirketler_df["mevcut_mesai"]))
    sabit_list = sirketler_df[sirketler_df["sabit"] == True]["isim"].tolist()

    def temiz(s):
        return "".join(c if c.isalnum() else "_" for c in str(s))

    prob = LpProblem("Optimizasyon", LpMinimize)
    x = {(s, saat): LpVariable(f"x_{temiz(s)}_{saat.replace(':','')}", cat="Binary")
         for s in sirketler_df["isim"] for saat in mesai_secenekleri}

    # Her şirket 1 saatte başlasın
    for s in sirketler_df["isim"]:
        prob += lpSum(x[s, saat] for saat in mesai_secenekleri) == 1

    # Max sapma kısıtı
    for s in sirketler_df["isim"]:
        mevcut = mesai_indeks.get(mesai_dict.get(s, "08:00"), 2)
        for saat in mesai_secenekleri:
            if abs(mesai_indeks[saat] - mevcut) > max_sapma:
                prob += x[s, saat] == 0

    # Sabit şirketler
    for s in sabit_list:
        mevcut = mesai_dict.get(s, "08:00")
        if mevcut in mesai_secenekleri:
            prob += x[s, mevcut] == 1

    # Her saatte min yük
    toplam_cal = guzergah_df["calisan_sayisi"].sum()
    for tepe_saat in ["08:00", "08:30", "09:00"]:
        prob += lpSum(
            x[s, tepe_saat] * guzergah_df[guzergah_df["sirket"]==s]["calisan_sayisi"].sum()
            for s in sirketler_df["isim"]
        ) >= toplam_cal * min_tepe_oran

    # Hedef: aynı ilçeden aynı saatte giden çalışanı minimize et
    hedef = []
    for ilce in guzergah_df["baslangic_ilce"].unique():
        ilce_guz = guzergah_df[guzergah_df["baslangic_ilce"] == ilce]
        for saat in mesai_secenekleri:
            for _, g in ilce_guz.iterrows():
                if g["sirket"] in sirketler_df["isim"].values:
                    hedef.append(x[g["sirket"], saat] * int(g["calisan_sayisi"]))

    prob += lpSum(hedef) if hedef else 0
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
    st.subheader("📋 Şirketler")
    st.dataframe(sirketler[["isim","mevcut_mesai","sabit"]], use_container_width=True, hide_index=True)
    
    st.subheader("🚌 Güzergahlar")
    ozet = guzergahlar.groupby("sirket").agg(
        guzergah_sayisi=("baslangic_ilce","count"),
        toplam_calisan=("calisan_sayisi","sum")
    ).reset_index()
    st.dataframe(ozet, use_container_width=True, hide_index=True)

    if st.button("🚀 Optimizasyonu Çalıştır", use_container_width=True, type="primary"):
        with st.spinner("Algoritma hesaplıyor..."):
            yeni_mesai = optimizasyon_calistir(sirketler, guzergahlar, max_sapma, min_tepe)
            st.session_state["yeni_mesai"]   = yeni_mesai
            st.session_state["sirketler"]    = sirketler
            st.session_state["guzergahlar"]  = guzergahlar

with col1:
    st.subheader("🗺️ Harita")
    m = folium.Map(location=[41.01, 28.96], zoom_start=11, tiles="CartoDB positron")

    renkler_liste = ["#E63946","#2196F3","#4CAF50","#FF9800","#9C27B0",
                     "#00BCD4","#F44336","#3F51B5","#8BC34A","#FF5722",
                     "#607D8B","#E91E63","#009688","#FFC107","#795548"]
    sirket_renk = {s: renkler_liste[i % len(renkler_liste)]
                   for i, s in enumerate(sirketler["isim"])}

    yeni_mesai = st.session_state.get("yeni_mesai", {})

    # Şirket merkezleri
    for _, s in sirketler.iterrows():
        eski = s["mevcut_mesai"]
        yeni = yeni_mesai.get(s["isim"], eski)
        degisti = eski != yeni
        folium.CircleMarker(
            [s["lat"], s["lon"]], radius=12,
            color=sirket_renk[s["isim"]], fill=True, fill_opacity=0.9,
            popup=folium.Popup(
                f"<b>{s['isim']}</b><br>"
                f"Eski mesai: {eski}<br>"
                f"Yeni mesai: {yeni}<br>"
                f"{'✅ Kaydırıldı' if degisti else '— Değişmedi'}",
                max_width=220)
        ).add_to(m)

    # Güzergah başlangıç noktaları
    for ilce in guzergahlar["baslangic_ilce"].unique():
        ilce_guz = guzergahlar[guzergahlar["baslangic_ilce"] == ilce]
        lat = ilce_guz["baslangic_lat"].iloc[0]
        lon = ilce_guz["baslangic_lon"].iloc[0]
        toplam = ilce_guz["calisan_sayisi"].sum()
        folium.CircleMarker(
            [lat, lon], radius=5 + min(toplam//20, 8),
            color="gray", fill=True, fill_opacity=0.5,
            popup=f"{ilce}\n{toplam} çalışan"
        ).add_to(m)

    # Güzergah çizgileri
    for _, g in guzergahlar.iterrows():
        if g["sirket"] in sirketler["isim"].values:
            sirket_row = sirketler[sirketler["isim"] == g["sirket"]].iloc[0]
            folium.PolyLine(
                [[g["baslangic_lat"], g["baslangic_lon"]],
                 [sirket_row["lat"], sirket_row["lon"]]],
                color=sirket_renk.get(g["sirket"], "gray"),
                weight=1.5, opacity=0.4,
                tooltip=f"{g['sirket']} | {g['baslangic_ilce']} → {int(g['calisan_sayisi'])} çalışan"
            ).add_to(m)

    legend = """<div style="position:fixed;bottom:20px;left:20px;background:white;
        padding:10px;border-radius:8px;border:1px solid #ccc;font-size:12px;">
        ⬤ Büyük: Şirket &nbsp; ⬤ Küçük: İlçe &nbsp; ─ Güzergah</div>"""
    m.get_root().html.add_child(folium.Element(legend))
    st_folium(m, height=450, use_container_width=True)

# ── SONUÇLAR ──
if "yeni_mesai" in st.session_state and st.session_state["yeni_mesai"]:
    st.markdown("---")
    st.subheader("📊 Optimizasyon Sonuçları")

    yeni_mesai  = st.session_state["yeni_mesai"]
    sirketler_s = st.session_state["sirketler"]
    guzergah_s  = st.session_state["guzergahlar"]
    mesai_dict  = dict(zip(sirketler_s["isim"], sirketler_s["mevcut_mesai"]))

    mevcut_skor, mevcut_detay = cakisma_hesapla(sirketler_s, guzergah_s, mesai_dict)
    yeni_skor,   yeni_detay   = cakisma_hesapla(sirketler_s, guzergah_s, yeni_mesai)
    azalma = (mevcut_skor - yeni_skor) / mevcut_skor * 100 if mevcut_skor > 0 else 0
    kaydirilan = sum(1 for s in sirketler_s["isim"] if mesai_dict.get(s) != yeni_mesai.get(s))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Eski Çakışma Skoru", f"{mevcut_skor:,}")
    m2.metric("Yeni Çakışma Skoru", f"{yeni_skor:,}", f"-{mevcut_skor-yeni_skor:,}")
    m3.metric("Azalma", f"%{azalma:.1f}")
    m4.metric("Kaydırılan Şirket", f"{kaydirilan} / {len(sirketler_s)}")

    # Sonuç tablosu
    sonuc_rows = []
    for s in sirketler_s["isim"]:
        eski = mesai_dict.get(s, "08:00")
        yeni = yeni_mesai.get(s, eski)
        guz  = guzergah_s[guzergah_s["sirket"] == s]
        sonuc_rows.append({
            "Şirket":        s,
            "Eski Mesai":    eski,
            "Yeni Mesai":    yeni,
            "Güzergah Sayısı": len(guz),
            "Toplam Çalışan": int(guz["calisan_sayisi"].sum()),
            "Durum":         "✅ Kaydırıldı" if eski != yeni else "— Aynı kaldı"
        })
    
    st.dataframe(pd.DataFrame(sonuc_rows), use_container_width=True, hide_index=True)

    # Grafik
    yuk_e = {s: 0 for s in mesai_secenekleri}
    yuk_y = {s: 0 for s in mesai_secenekleri}
    for _, g in guzergah_s.iterrows():
        if g["sirket"] in mesai_dict:
            yuk_e[mesai_dict[g["sirket"]]]  += int(g["calisan_sayisi"])
            yuk_y[yeni_mesai.get(g["sirket"], mesai_dict[g["sirket"]])] += int(g["calisan_sayisi"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 4))

    xp = range(len(mesai_secenekleri))
    ax1.plot(xp, list(yuk_e.values()), "o-", color="#E63946", lw=2.5, ms=8, label="Önce")
    ax1.plot(xp, list(yuk_y.values()), "o-", color="#4CAF50", lw=2.5, ms=8, label="Sonra")
    ax1.fill_between(xp, list(yuk_e.values()), list(yuk_y.values()), alpha=0.12, color="#4CAF50")
    ax1.set_xticks(list(xp)); ax1.set_xticklabels(mesai_secenekleri, rotation=30)
    ax1.set_title("Saate Göre Çalışan Yükü", fontweight="bold")
    ax1.set_ylabel("Çalışan Sayısı"); ax1.legend(); ax1.grid(alpha=0.3)
    ax1.spines[["top","right"]].set_visible(False)

    ax2.bar(["Önce","Sonra"], [mevcut_skor, yeni_skor],
            color=["#E63946","#4CAF50"], width=0.5, edgecolor="white")
    ax2.set_title("Toplam Çakışma Skoru", fontweight="bold")
    for i, v in enumerate([mevcut_skor, yeni_skor]):
        ax2.text(i, v + 100, f"{v:,}", ha="center", fontweight="bold")
    ax2.set_ylim(0, mevcut_skor * 1.15)
    ax2.spines[["top","right"]].set_visible(False)

    st.pyplot(fig)

    if not mevcut_detay.empty:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**En çakışan 10 nokta (önce):**")
            st.dataframe(mevcut_detay.head(10), use_container_width=True, hide_index=True)
        with col_b:
            st.markdown("**En çakışan 10 nokta (sonra):**")
            if not yeni_detay.empty:
                st.dataframe(yeni_detay.head(10), use_container_width=True, hide_index=True)
            else:
                st.success("Çakışma kalmadı!")
