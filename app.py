import streamlit as st
import pandas as pd
import numpy as np
import folium
import requests as req
from streamlit_folium import st_folium
from pulp import *
from collections import defaultdict
import matplotlib.pyplot as plt
import re
import datetime
import trafik_model as tm   # sıkışıklık-duyarlı denge motoru

st.set_page_config(page_title="İstanbul Trafik Optimizasyonu", layout="wide")
st.title("İstanbul Trafik Optimizasyonu")
st.markdown("Şirket mesai saatlerini optimize ederek İstanbul'daki trafik yükünü azalt.")

# ── SABİTLER ──
mesai_secenekleri = ["07:00","07:30","08:00","08:30","09:00","09:30","10:00"]
mesai_indeks      = {s: i for i, s in enumerate(mesai_secenekleri)}
RENKLER = ["#E63946","#2196F3","#4CAF50","#FF9800","#9C27B0",
           "#00BCD4","#F44336","#3F51B5","#8BC34A","#FF5722",
           "#607D8B","#E91E63","#009688","#FFC107","#795548"]

# ── VERİ TEMİZLEME ──
def mesai_formatla(deger):
    if pd.isna(deger):
        return "08:00"
    
    # Eğer datetime/time nesnesi ise
    if hasattr(deger, "hour") and hasattr(deger, "minute"):
        return f"{str(deger.hour).zfill(2)}:{str(deger.minute).zfill(2)}"
        
    s = str(deger).strip()
    # Regex ile HH:MM formatını ara
    match = re.search(r"(\d{1,2}):(\d{2})", s)
    if match:
        return f"{match.group(1).zfill(2)}:{match.group(2)}"
        
    return "08:00"

def df_temizle_sirket(df):
    d = df.copy()
    d["lat"]          = d["lat"].astype(float)
    d["lon"]          = d["lon"].astype(float)
    d["isim"]         = d["isim"].astype(str)
    d["mevcut_mesai"] = d["mevcut_mesai"].apply(mesai_formatla)
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
    konum = {}
    for _, s in sirket_df.iterrows():
        konum[str(s["isim"])] = (float(s["lat"]), float(s["lon"]))
    d = guzergah_df.copy()
    d["sirket_lat"] = d["sirket"].apply(lambda s: konum.get(str(s), (41.0, 29.0))[0])
    d["sirket_lon"] = d["sirket"].apply(lambda s: konum.get(str(s), (41.0, 29.0))[1])
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

def guzergah_rotalarini_ekle(guzergah_df, sirket_df):
    d = guzergaha_sirket_konum_ekle(guzergah_df, sirket_df)
    coords_list = []
    durations_list = []
    
    bar = st.progress(0)
    status_text = st.empty()
    total = len(d)
    
    status_text.text("Rotalar OSRM sunucusundan çekiliyor...")
    for idx, row in d.iterrows():
        coords, duration = gercek_rota(
            float(row["baslangic_lat"]), float(row["baslangic_lon"]),
            float(row["sirket_lat"]),    float(row["sirket_lon"])
        )
        coords_list.append(coords)
        durations_list.append(duration)
        bar.progress(min(1.0, (idx + 1) / total))
        
    bar.empty()
    status_text.empty()
    
    d["route_coords"] = coords_list
    d["base_duration"] = durations_list
    return d

# ── IBB HIZ TABLOSU ──
@st.cache_data(show_spinner=False)
def hiz_tablosunu_yukle():
    try:
        from ibb_hiz_tablosu import IBB_HIZ_TABLOSU
        return IBB_HIZ_TABLOSU
    except:
        return {}

def bolge_hizi_bul(lat, lon, saat_str):
    """IBB verisinden koordinata en yakın bölgenin saatlik hızını döndür."""
    saat_int = int(saat_str.split(":")[0])
    hiz_tablo = hiz_tablosunu_yukle()
    en_yakin_mesafe = float("inf")
    baz_hiz = 30.0
    for key, saatlik in hiz_tablo.items():
        try:
            b_lat, b_lon = float(key[0]), float(key[1])
        except:
            continue
        mesafe = abs(b_lat - lat) + abs(b_lon - lon)
        if mesafe < en_yakin_mesafe:
            en_yakin_mesafe = mesafe
            baz_hiz = float(saatlik.get(saat_int, saatlik.get(str(saat_int), 30.0)))
    return baz_hiz

# ── SÜRE HESABI ──
def sure_hesapla(guzergah_df, mesai_dict):
    toplam_sure = 0.0
    toplam_kisi = 0
    detay = []
    for _, g in guzergah_df.iterrows():
        sirket  = str(g["sirket"])
        saat    = mesai_dict.get(sirket, "08:00")
        ort_lat = (float(g["baslangic_lat"]) + float(g["sirket_lat"])) / 2
        ort_lon = (float(g["baslangic_lon"]) + float(g["sirket_lon"])) / 2
        hiz_kmh = bolge_hizi_bul(ort_lat, ort_lon, saat)
        
        # OSRM çağrısı yerine DataFrame'den oku!
        sure_sn = float(g["base_duration"])
        
        mesafe_km   = (sure_sn / 3600) * 80
        gercek_sure = (mesafe_km / hiz_kmh) * 60 if hiz_kmh > 0 else 45.0
        kisi = int(g["calisan_sayisi"])
        toplam_sure += gercek_sure * kisi
        toplam_kisi += kisi
        detay.append({
            "Şirket":            sirket,
            "İlçe":              str(g["baslangic_ilce"]),
            "Mesai":             saat,
            "Bölge Hızı (km/h)": round(hiz_kmh, 1),
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
    return toplam, pd.DataFrame(detay).sort_values("Yük", ascending=False) if detay else pd.DataFrame()

# ── OPTİMİZASYON ──
def guzergah_surelerini_hesapla(guzergah_df):
    """Her güzergah için tüm mesai saatlerindeki IBB hızına göre süreyi hesapla."""
    sureler = {}
    for _, g in guzergah_df.iterrows():
        sirket  = str(g["sirket"])
        ilce    = str(g["baslangic_ilce"])
        ort_lat = (float(g["baslangic_lat"]) + float(g["sirket_lat"])) / 2
        ort_lon = (float(g["baslangic_lon"]) + float(g["sirket_lon"])) / 2
        
        # OSRM çağrısı yerine DataFrame'den oku!
        sure_sn = float(g["base_duration"])
        
        mesafe_km = (sure_sn / 3600) * 80
        for saat in mesai_secenekleri:
            hiz = bolge_hizi_bul(ort_lat, ort_lon, saat)
            sureler[(sirket, ilce, saat)] = round((mesafe_km / hiz) * 60 if hiz > 0 else 45.0, 2)
    return sureler

def optimizasyon_calistir(sirketler_df, guzergah_df, max_sapma, max_saatlik_oran, mod,
                          doluluk=1.5, tolerans=1.5, alfa=0.50, carpan_max=2.5,
                          senaryo="Normal"):
    """
    Sıkışıklık-duyarlı iteratif DENGE optimizasyonu (senaryo destekli).

    Hızlar sabit değildir: çalışan yükü arttıkça o saat-bölgenin hızı düşer
    (volume-delay). Kapasite veriden otomatik kalibre edilir, yavaşlama
    carpan_max ile sınırlıdır. 'senaryo' Normal dışında bir geçiş kapatırsa
    (1. Köprü / Avrasya Tüneli), o geçişi kullanan rotalar en yakın açık
    geçişe yönlendirilir (detour + alternatif geçişe yük yığılması).
    Dönüş: tam rapor sözlüğü (önce/sonra İKİSİ DE sıkışıklık dahil).
    """
    return tm.calistir_denge_optimizasyon(
        sirketler_df, guzergah_df, mesai_secenekleri,
        max_sapma, max_saatlik_oran, mod,
        alfa=alfa, beta=2.0, tolerans=tolerans, carpan_max=carpan_max,
        doluluk=doluluk, senaryo=senaryo, max_iter=15,
    )

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
st.sidebar.header("Ayarlar")
st.sidebar.subheader("Excel Veri Yükle")
yuklenen = st.sidebar.file_uploader("Excel dosyası (.xlsx)", type=["xlsx"])

# Dosya yükleme state yönetimi
dosya_adi = yuklenen.name if yuklenen else None
eski_dosya = st.session_state.get("dosya_adi", None)

# Eğer sayfa ilk defa açılıyorsa veya dosya değiştiyse
if dosya_adi != eski_dosya or "sirketler" not in st.session_state or "guzergahlar" not in st.session_state:
    st.session_state["dosya_adi"] = dosya_adi
    if "yeni_mesai" in st.session_state:
        del st.session_state["yeni_mesai"]
    if "opt_status" in st.session_state:
        del st.session_state["opt_status"]
        
    if yuklenen:
        try:
            xl = pd.ExcelFile(yuklenen)
            if "sirketler" in xl.sheet_names and "guzergahlar" in xl.sheet_names:
                sirketler_clean = df_temizle_sirket(xl.parse("sirketler"))
                guzergahlar_clean = df_temizle_guzergah(xl.parse("guzergahlar"))
                
                st.session_state["sirketler"] = sirketler_clean
                st.session_state["guzergahlar"] = guzergah_rotalarini_ekle(guzergahlar_clean, sirketler_clean)
                st.session_state["excel_basarili"] = True
                st.session_state["excel_mesaj"] = f"{len(sirketler_clean)} şirket, {len(guzergahlar_clean)} güzergah yüklendi!"
            else:
                st.session_state["excel_basarili"] = False
                st.session_state["excel_mesaj"] = "'sirketler' ve 'guzergahlar' sayfaları gerekli!"
                st.session_state["sirketler"] = df_temizle_sirket(VARSAYILAN_SIRKETLER)
                st.session_state["guzergahlar"] = guzergah_rotalarini_ekle(df_temizle_guzergah(VARSAYILAN_GUZERGAHLAR), st.session_state["sirketler"])
        except Exception as e:
            st.session_state["excel_basarili"] = False
            st.session_state["excel_mesaj"] = f"Hata: {e}"
            st.session_state["sirketler"] = df_temizle_sirket(VARSAYILAN_SIRKETLER)
            st.session_state["guzergahlar"] = guzergah_rotalarini_ekle(df_temizle_guzergah(VARSAYILAN_GUZERGAHLAR), st.session_state["sirketler"])
    else:
        st.session_state["sirketler"] = df_temizle_sirket(VARSAYILAN_SIRKETLER)
        st.session_state["guzergahlar"] = guzergah_rotalarini_ekle(df_temizle_guzergah(VARSAYILAN_GUZERGAHLAR), st.session_state["sirketler"])

# Sidebar mesajları
if yuklenen:
    if st.session_state.get("excel_basarili", False):
        st.sidebar.success(st.session_state["excel_mesaj"])
    else:
        st.sidebar.error(st.session_state["excel_mesaj"])
else:
    st.sidebar.info("Varsayılan 15 şirket kullanılıyor.")

with st.sidebar.expander("Excel Formatı"):
    st.markdown("""
**sirketler sayfası:** isim, lat, lon, mevcut_mesai, sabit

**guzergahlar sayfası:** sirket, baslangic_ilce, baslangic_lat, baslangic_lon, calisan_sayisi
    """)

max_sapma = st.sidebar.slider("Max mesai kayması (adım)", 1, 4, 2, help="1 adım = 30 dk")
max_saatlik_oran = st.sidebar.slider("Saatlik maks. çalışan kapasitesi (%)", 10, 50, 25, help="Hiçbir saat diliminde toplam çalışanların bu oranından fazlası işe başlamasın.") / 100

st.sidebar.markdown("---")
st.sidebar.subheader("Optimizasyon Modu")
opt_mod = st.sidebar.radio(
    "Hedef fonksiyon:",
    options=["uzun_sure", "peak_yuk", "ortalama_sure"],
    format_func=lambda x: {
        "uzun_sure":     "En uzun süreyi kısalt",
        "peak_yuk":      "Tepe saatteki yükü azalt",
        "ortalama_sure": "Ortalama süreyi kısalt"
    }[x]
)

st.sidebar.markdown("---")
st.sidebar.subheader("Sıkışıklık Modeli")
st.sidebar.caption("İBB saatlik hızı arka plan kabul edilir; çalışanlarımız ek yük oluşturur ve yığılınca o saatin hızı düşer. Kapasite veriden otomatik kalibre edilir, yavaşlamanın bir üst sınırı vardır.")
doluluk = st.sidebar.slider("Araç başına kişi (doluluk)", 1.0, 3.0, 1.5, 0.1,
    help="Servis/araç doluluğu. Yüksek değer = aynı çalışan için daha az araç = daha az sıkışıklık.")
tolerans = st.sidebar.slider("Yığılma toleransı", 0.5, 3.0, 1.5, 0.1,
    help="Kapasite = tolerans × (mevcut yüklerin referansı). Düşük değer = yığılma daha erken cezalandırılır.")
alfa = st.sidebar.slider("Sıkışıklık duyarlılığı (α)", 0.1, 1.0, 0.5, 0.05,
    help="Volume-delay katsayısı (β=2 sabit). Yüksek = yığılmanın hıza etkisi daha belirgin.")
carpan_max = st.sidebar.slider("Maks. yavaşlama (×)", 1.5, 4.0, 2.5, 0.1,
    help="Bir yolun en fazla kaç kat yavaşlayabileceği. Sürelerin gerçek dışı şişmesini engeller.")

st.sidebar.markdown("---")
st.sidebar.subheader("Senaryolar")
st.sidebar.caption("Bir geçiş kapanınca onu kullanan rotalar en yakın açık geçişe yönlendirilir (yol uzar + alternatif geçiş tıkanır). Algoritmanın bu duruma tepkisini gör.")
senaryo = st.sidebar.radio(
    "Aktif senaryo:",
    options=list(tm.SENARYOLAR.keys()),
    help="Normal = bugünkü durum (hiçbir kapanma). Diğerleri ilgili geçişi trafiğe kapatır.",
)

sirketler = st.session_state["sirketler"]
guzergahlar = st.session_state["guzergahlar"]
sirket_renk = {str(r["isim"]): RENKLER[i % len(RENKLER)]
               for i, (_, r) in enumerate(sirketler.iterrows())}

# ── ANA EKRAN ──
col1, col2 = st.columns([3, 2])

with col2:
    st.subheader("Şirketler")
    st.dataframe(sirketler[["isim","mevcut_mesai","sabit"]], use_container_width=True, hide_index=True)

    ozet = guzergahlar.groupby("sirket").agg(
        guzergah=("baslangic_ilce","count"),
        calisan=("calisan_sayisi","sum")
    ).reset_index()
    st.subheader("Güzergah Özeti")
    st.dataframe(ozet, use_container_width=True, hide_index=True)

    if st.button("Optimizasyonu Çalıştır", use_container_width=True, type="primary"):
        with st.spinner("Sıkışıklık dengesi hesaplanıyor (iteratif)..."):
            sonuc = optimizasyon_calistir(sirketler, guzergahlar, max_sapma,
                                          max_saatlik_oran, opt_mod,
                                          doluluk, tolerans, alfa, carpan_max,
                                          senaryo)
            if sonuc["status"] == "Optimal":
                st.session_state["opt_sonuc"]   = sonuc
                st.session_state["yeni_mesai"]  = sonuc["yeni_mesai"]
                st.session_state["sirketler"]   = sirketler
                st.session_state["guzergahlar"] = guzergahlar
                st.session_state["opt_status"]  = "Optimal"
            else:
                st.session_state["opt_status"]  = sonuc["status"]
                st.session_state.pop("yeni_mesai", None)
                st.session_state.pop("opt_sonuc", None)

    if st.button("🚧 Senaryoları Karşılaştır", use_container_width=True,
                 help="Normal / 1. Köprü Kapalı / Avrasya Tüneli Kapalı senaryolarını aynı ayarlarla çalıştırıp karşılaştırır."):
        with st.spinner("Üç senaryo çalıştırılıyor..."):
            kars = []
            for sen in tm.SENARYOLAR.keys():
                s = optimizasyon_calistir(sirketler, guzergahlar, max_sapma,
                                          max_saatlik_oran, opt_mod,
                                          doluluk, tolerans, alfa, carpan_max, sen)
                kars.append({
                    "Senaryo": sen,
                    "Etkilenen Rota": s["etkilenen_rota"],
                    "Optimizasyon Öncesi (dk)": s["ort_eski"],
                    "Optimizasyon Sonrası (dk)": s["ort_yeni"],
                })
            st.session_state["senaryo_kars"] = pd.DataFrame(kars)

with col1:
    st.subheader("Harita")
    
    # Solver hata durumunu göster
    opt_status = st.session_state.get("opt_status", None)
    if opt_status and opt_status != "Optimal":
        st.error(f"Optimizasyon çözülemedi (Durum: {opt_status})! Lütfen kısıtlamaları (Max sapma veya Saatlik maks. çalışan kapasitesi) esnetip tekrar deneyin.")
        
    yeni_mesai = st.session_state.get("yeni_mesai", {})
    opt_sonuc  = st.session_state.get("opt_sonuc", None)
    m = folium.Map(location=[41.01, 28.96], zoom_start=11, tiles="CartoDB positron")

    for gidx, g in guzergahlar.iterrows():
        sirket_adi = str(g["sirket"])
        if sirket_adi not in sirketler["isim"].values:
            continue
        s_row = sirketler[sirketler["isim"] == sirket_adi].iloc[0]

        koordinatlar = g["route_coords"]
        # mesafe: rota poligonundan (haversine), 80km/h varsayımı yerine gerçek
        if koordinatlar and len(koordinatlar) > 1:
            mesafe_km = round(sum(
                tm.haversine_km(koordinatlar[i][0], koordinatlar[i][1],
                                koordinatlar[i+1][0], koordinatlar[i+1][1])
                for i in range(len(koordinatlar) - 1)), 1)
        else:
            mesafe_km = 0.0

        eski_saat = str(s_row["mevcut_mesai"])
        yeni_saat = yeni_mesai.get(sirket_adi, eski_saat)

        if opt_sonuc:
            # sıkışıklık-dahil önce/sonra süreler (denge motorundan)
            sure_eski = opt_sonuc["rota_sure_eski"].get(gidx, 0)
            sure_yeni = opt_sonuc["rota_sure_yeni"].get(gidx, 0)
            fark      = round(sure_eski - sure_yeni, 1)
            fark_str  = (f"{fark} dk kazanıldı" if fark > 0
                         else ("— Aynı" if fark == 0 else f"{abs(fark)} dk arttı"))
            tooltip_text = (
                f"<b>{sirket_adi}</b><br>"
                f"{str(g['baslangic_ilce'])} → {sirket_adi}<br>"
                f"📏 Mesafe: {mesafe_km} km<br>"
                f"⏱ Önce ({eski_saat}): {sure_eski} dk<br>"
                f"⏱ Sonra ({yeni_saat}): {sure_yeni} dk<br>"
                f"{fark_str}<br><i>(sıkışıklık dahil)</i>"
            )
        else:
            tooltip_text = (
                f"<b>{sirket_adi}</b><br>"
                f"{str(g['baslangic_ilce'])} → {sirket_adi}<br>"
                f"📏 Mesafe: {mesafe_km} km<br>"
                f"⏱ Mevcut mesai: {eski_saat}<br>"
                f"<i>Süre için optimizasyonu çalıştırın</i>"
            )

        folium.PolyLine(
            locations=koordinatlar,
            color=sirket_renk.get(sirket_adi, "gray"),
            weight=2, opacity=0.5,
            tooltip=folium.Tooltip(tooltip_text)
        ).add_to(m)

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

    for _, s in sirketler.iterrows():
        isim    = str(s["isim"])
        eski    = str(s["mevcut_mesai"])
        yeni    = yeni_mesai.get(isim, eski)
        degisti = eski != yeni
        folium.CircleMarker(
            location=[float(s["lat"]), float(s["lon"])],
            radius=12,
            color=sirket_renk.get(isim, "#333"),
            fill=True, fill_opacity=0.9,
            popup=folium.Popup(
                f"<b>{isim}</b><br>Eski: {eski}<br>Yeni: {yeni}<br>"
                f"{'Kaydırıldı' if degisti else '— Değişmedi'}",
                max_width=220)
        ).add_to(m)

    st_folium(m, height=450, use_container_width=True)

# ── SONUÇLAR ──
if "yeni_mesai" in st.session_state and st.session_state["yeni_mesai"]:
    st.markdown("---")
    mod_label = {"uzun_sure":"En Uzun Süreyi Kısalt",
                 "peak_yuk":"Tepe Saatteki Yükü Azalt",
                 "ortalama_sure":"Ortalama Süreyi Kısalt"}
    aktif_senaryo = st.session_state.get("opt_sonuc", {}).get("senaryo", "Normal")
    baslik = f"Optimizasyon Sonuçları — {mod_label.get(opt_mod,'')}"
    if aktif_senaryo != "Normal":
        baslik += f"  |  🚧 {aktif_senaryo}"
    st.subheader(baslik)
    etk = st.session_state.get("opt_sonuc", {}).get("etkilenen_rota", 0)
    if aktif_senaryo != "Normal":
        st.warning(f"**{aktif_senaryo}** senaryosu aktif: bu geçişi kullanan **{etk} rota** "
                   f"en yakın açık geçişe yönlendirildi (yol uzadı + alternatif geçiş tıkandı). "
                   f"Aşağıdaki tüm değerler bu kapanmayı içerir.")

    yeni_mesai  = st.session_state["yeni_mesai"]
    opt_sonuc   = st.session_state.get("opt_sonuc", {})
    sirketler_s = df_temizle_sirket(st.session_state["sirketler"])
    guzergah_s  = st.session_state["guzergahlar"]
    mesai_dict  = {str(r["isim"]): str(r["mevcut_mesai"]) for _, r in sirketler_s.iterrows()}

    mevcut_skor, _ = cakisma_hesapla(guzergah_s, mesai_dict)
    yeni_skor,   _ = cakisma_hesapla(guzergah_s, yeni_mesai)
    azalma         = (mevcut_skor - yeni_skor) / mevcut_skor * 100 if mevcut_skor > 0 else 0
    kaydirilan     = sum(1 for s in sirketler_s["isim"] if mesai_dict.get(str(s)) != yeni_mesai.get(str(s)))

    # Süreler: ÖNCE ve SONRA — ikisi de sıkışıklık dahil (denge motorundan)
    ort_sure_eski = opt_sonuc.get("ort_eski", 0.0)
    ort_sure_yeni = opt_sonuc.get("ort_yeni", 0.0)
    detay_eski    = opt_sonuc.get("detay_eski", pd.DataFrame())
    detay_yeni    = opt_sonuc.get("detay_yeni", pd.DataFrame())
    sure_fark     = ort_sure_eski - ort_sure_yeni

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Eski Çakışma",      f"{mevcut_skor:,}")
    m2.metric("Yeni Çakışma",      f"{yeni_skor:,}", f"{yeni_skor-mevcut_skor:+,}")
    m3.metric("Çakışma Azalması",  f"%{azalma:.1f}")
    m4.metric("Kaydırılan Şirket", f"{kaydirilan}/{len(sirketler_s)}")
    m5.metric("Eski Ort. Süre",    f"{ort_sure_eski} dk")
    m6.metric("Yeni Ort. Süre",    f"{ort_sure_yeni} dk", f"{-sure_fark:.1f} dk")

    gecmis = opt_sonuc.get("gecmis", [])
    st.caption(
        "ℹ️ Her iki süre de **sıkışıklık dahil** hesaplanır: çalışan yükü arttıkça "
        "o saat-bölgedeki hız düşer. Atama, yeni yığılma yaratmamak için "
        f"**{len(gecmis)} iterasyonlu denge** ile bulunmuştur "
        "(böylece kazanç, eski sürümdeki gibi 'sabit hız' varsayımıyla şişirilmez)."
    )

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
            "Durum":     "Kaydırıldı" if eski != yeni else "— Aynı"
        })
    st.dataframe(pd.DataFrame(sonuc_rows), use_container_width=True, hide_index=True)

    with st.expander("Güzergah Bazlı Süre Detayı"):
        cx, cy = st.columns(2)
        with cx:
            st.markdown("**Önce (mevcut mesai):**")
            st.dataframe(detay_eski.sort_values("Tahmini Süre (dk)", ascending=False),
                         use_container_width=True, hide_index=True)
        with cy:
            st.markdown("**Sonra (optimize):**")
            st.dataframe(detay_yeni.sort_values("Tahmini Süre (dk)", ascending=False),
                         use_container_width=True, hide_index=True)

    # Grafikler
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

    if gecmis:
        with st.expander("Denge Yakınsaması (iterasyon başına ortalama süre)"):
            st.caption("İteratif denge (MSA): atama sabitlenene kadar yükler güncellenir. "
                       "Düz bir çizgiye oturması yakınsamayı gösterir.")
            gdf = pd.DataFrame(gecmis).set_index("iter")
            st.line_chart(gdf[["ort_sure"]])
            st.dataframe(gdf.reset_index().rename(columns={
                "iter": "İterasyon", "ort_sure": "Ort. Süre (dk)", "degisen": "Değişen Atama"
            }), use_container_width=True, hide_index=True)

# ── SENARYO KARŞILAŞTIRMASI ──
if "senaryo_kars" in st.session_state:
    st.markdown("---")
    st.subheader("🚧 Senaryo Karşılaştırması — Geçiş Kapanmaları")
    st.caption("Aynı ayarlarla üç senaryo: bir köprü/tünel kapanınca o geçişi kullanan rotalar "
               "en yakın açık geçişe yönlenir; yol uzar ve alternatif geçiş tıkanır. "
               "Tablo, sistemin (optimizasyonun) bu kırılganlığa nasıl tepki verdiğini gösterir.")
    kdf = st.session_state["senaryo_kars"].copy()
    normal_sonra = kdf.loc[kdf["Senaryo"] == "Normal", "Optimizasyon Sonrası (dk)"].iloc[0]
    kdf["Normal'e Göre Fark"] = (kdf["Optimizasyon Sonrası (dk)"] - normal_sonra).round(1)

    st.dataframe(kdf, use_container_width=True, hide_index=True)

    figk, axk = plt.subplots(figsize=(10, 4))
    xk = np.arange(len(kdf))
    axk.bar(xk - 0.2, kdf["Optimizasyon Öncesi (dk)"], 0.38, label="Optimizasyon Öncesi", color="#E63946", alpha=0.85)
    axk.bar(xk + 0.2, kdf["Optimizasyon Sonrası (dk)"], 0.38, label="Optimizasyon Sonrası", color="#4CAF50", alpha=0.85)
    for i, (e, y) in enumerate(zip(kdf["Optimizasyon Öncesi (dk)"], kdf["Optimizasyon Sonrası (dk)"])):
        axk.text(i - 0.2, e + 0.4, f"{e}", ha="center", fontsize=9, fontweight="bold")
        axk.text(i + 0.2, y + 0.4, f"{y}", ha="center", fontsize=9, fontweight="bold")
    axk.axhline(normal_sonra, color="#888", ls="--", lw=1, label="Normal (referans)")
    axk.set_xticks(xk); axk.set_xticklabels(kdf["Senaryo"], rotation=10)
    axk.set_ylabel("Ortalama İşe Gidiş Süresi (dk)")
    axk.set_title("Senaryolara Göre Ortalama Süre", fontweight="bold")
    axk.legend(fontsize=9); axk.grid(alpha=0.3, axis="y")
    axk.spines[["top","right"]].set_visible(False)
    st.pyplot(figk)

    en_kotu = kdf.sort_values("Optimizasyon Sonrası (dk)", ascending=False).iloc[0]
    if en_kotu["Senaryo"] != "Normal":
        fark_val = float(en_kotu["Normal'e Göre Fark"])
        etk_val = int(en_kotu["Etkilenen Rota"])
        sen_ad = en_kotu["Senaryo"]
        st.info(f"En çok etkilenen senaryo: **{sen_ad}** — ortalama süre Normal'e göre "
                f"**+{fark_val:.1f} dk**, **{etk_val} rota** yönlendirildi. "
                f"Optimizasyon kapanmanın etkisini bir miktar azaltsa da tamamen telafi edemez; "
                f"bu, geçişin sistem için ne kadar kritik olduğunu gösterir.")
