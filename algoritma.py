# =============================================================
# İSTANBUL TRAFİK OPTİMİZASYONU — Rota Çakışması Minimizasyonu
# Bitirme Projesi | Mayıs 2026
# =============================================================
# KURULUM:
#   pip install osmnx networkx folium scipy pulp pandas matplotlib
#
# ÇALIŞTIRMA:
#   python algoritma.py
# =============================================================

import osmnx as ox
import networkx as nx
import pandas as pd
import numpy as np
import folium
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pulp import *
from math import radians, sin, cos, sqrt, atan2
from collections import defaultdict

# ── 1. VERİ SETİ ──────────────────────────────────────────────

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

sirketler = pd.DataFrame([
    {"isim": "TechPark A.Ş.",      "lat": 41.0500, "lon": 29.0200},
    {"isim": "Finans Bank",         "lat": 41.0694, "lon": 29.0104},
    {"isim": "Medya Grubu",         "lat": 41.0482, "lon": 28.9912},
    {"isim": "Yazılım Ltd.",        "lat": 41.0321, "lon": 29.0050},
    {"isim": "Sigorta A.Ş.",        "lat": 41.0234, "lon": 28.9543},
    {"isim": "Lojistik Ltd.",       "lat": 41.0391, "lon": 28.8378},
    {"isim": "Üretim A.Ş.",         "lat": 40.9927, "lon": 29.0277},
    {"isim": "Danışmanlık Ltd.",    "lat": 41.0462, "lon": 29.0338},
    {"isim": "E-Ticaret A.Ş.",      "lat": 41.0123, "lon": 28.9234},
    {"isim": "Sağlık Grubu",        "lat": 40.9821, "lon": 29.0123},
    {"isim": "Eğitim Ltd.",         "lat": 41.0654, "lon": 28.8976},
    {"isim": "Enerji A.Ş.",         "lat": 41.0321, "lon": 28.8654},
    {"isim": "Perakende Ltd.",      "lat": 40.9654, "lon": 28.7923},
    {"isim": "Turizm A.Ş.",         "lat": 41.0987, "lon": 28.9432},
    {"isim": "İnşaat Grubu",        "lat": 41.0543, "lon": 28.9876},
])

baslangic_mesai = {
    "TechPark A.Ş.":   "08:00", "Finans Bank":      "09:00",
    "Medya Grubu":     "09:00", "Yazılım Ltd.":     "08:00",
    "Sigorta A.Ş.":    "08:30", "Lojistik Ltd.":    "07:30",
    "Üretim A.Ş.":     "08:00", "Danışmanlık Ltd.": "09:30",
    "E-Ticaret A.Ş.":  "08:00", "Sağlık Grubu":     "08:30",
    "Eğitim Ltd.":     "08:00", "Enerji A.Ş.":      "07:00",
    "Perakende Ltd.":  "09:00", "Turizm A.Ş.":      "10:00",
    "İnşaat Grubu":    "07:30",
}

sabit_sirketler  = ["Sağlık Grubu", "Lojistik Ltd."]
mesai_secenekleri = ["07:00","07:30","08:00","08:30","09:00","09:30","10:00"]
mesai_indeks      = {s: i for i, s in enumerate(mesai_secenekleri)}

# ── 2. GÜZERGAH SİMÜLASYONU ───────────────────────────────────

np.random.seed(42)
ilce_listesi = list(ilceler.keys())
guzergahlar  = []

for _, sirket in sirketler.iterrows():
    kac = np.random.randint(3, 6)
    for ilce in np.random.choice(ilce_listesi, kac, replace=False):
        guzergahlar.append({
            "sirket":        sirket["isim"],
            "sirket_lat":    sirket["lat"],
            "sirket_lon":    sirket["lon"],
            "baslangic_ilce": ilce,
            "baslangic_lat": ilceler[ilce][0],
            "baslangic_lon": ilceler[ilce][1],
            "calisan_sayisi": 20,
        })

guzergah_df = pd.DataFrame(guzergahlar)
print(f"Toplam güzergah: {len(guzergah_df)} | Toplam çalışan: {len(guzergah_df)*20}")

# ── 3. YOL AĞI & ROTA HESABI ──────────────────────────────────

print("\nİstanbul yol ağı yükleniyor...")
G = ox.graph_from_place("Istanbul, Turkey", network_type="drive")
print(f"Kavşak: {len(G.nodes):,} | Segment: {len(G.edges):,}")

print("\nRotalar hesaplanıyor...")
rotalar = []
for i, row in guzergah_df.iterrows():
    try:
        orig = ox.nearest_nodes(G, row["baslangic_lon"], row["baslangic_lat"])
        dest = ox.nearest_nodes(G, row["sirket_lon"],    row["sirket_lat"])
        rota = nx.shortest_path(G, orig, dest, weight="travel_time")
        kenarlar = [(rota[j], rota[j+1]) for j in range(len(rota)-1)]
        mesafe = sum(G[u][v][0].get("length", 0) for u, v in kenarlar) / 1000
        rotalar.append({
            "sirket":         row["sirket"],
            "baslangic_ilce": row["baslangic_ilce"],
            "kenarlar":       kenarlar,
            "mesafe_km":      round(mesafe, 1),
            "calisan_sayisi": row["calisan_sayisi"],
        })
    except:
        pass

rota_df = pd.DataFrame(rotalar)
print(f"Tamamlandı: {len(rota_df)} rota | Ort. mesafe: {rota_df['mesafe_km'].mean():.1f} km")

# ── 4. ÇAKIŞMA SKORU FONKSİYONLARI ───────────────────────────

def segment_yuklerini_hesapla(mesai_dict, rota_df):
    segment_yuk = defaultdict(lambda: defaultdict(int))
    for _, rota in rota_df.iterrows():
        mesai = mesai_dict[rota["sirket"]]
        for kenar in rota["kenarlar"]:
            seg = (min(kenar[0], kenar[1]), max(kenar[0], kenar[1]))
            segment_yuk[seg][mesai] += rota["calisan_sayisi"]
    return segment_yuk

def cakisma_skoru_hesapla(segment_yuk):
    toplam = 0
    kritik = []
    for seg, saatler in segment_yuk.items():
        for saat, yuk in saatler.items():
            if yuk > 20:
                toplam += yuk
                kritik.append({"segment": seg, "saat": saat, "yuk": yuk})
    kritik_df = pd.DataFrame(kritik).sort_values("yuk", ascending=False) if kritik else pd.DataFrame()
    return toplam, kritik_df

# Mevcut durum
mevcut_yuk            = segment_yuklerini_hesapla(baslangic_mesai, rota_df)
mevcut_skor, mevcut_k = cakisma_skoru_hesapla(mevcut_yuk)
print(f"\nMevcut çakışma skoru: {mevcut_skor:,} | Kritik segment: {len(mevcut_k)}")

# ── 5. OPTİMİZASYON ALGORİTMASI ──────────────────────────────

print("\nOptimizasyon çalışıyor...")
prob = LpProblem("Rota_Cakisma_Min", LpMinimize)

def temiz(s):
    return s.replace(" ","_").replace(".","").replace("İ","I").replace("ş","s").replace("ç","c").replace("ğ","g").replace("ü","u").replace("ö","o")

x = {(s, saat): LpVariable(f"x_{temiz(s)}_{saat.replace(':','')}", cat="Binary")
     for s in sirketler["isim"] for saat in mesai_secenekleri}

# Her şirket 1 saatte başlasın
for s in sirketler["isim"]:
    prob += lpSum(x[s, saat] for saat in mesai_secenekleri) == 1

# Max ±2 adım sapma
for s in sirketler["isim"]:
    mevcut = mesai_indeks.get(baslangic_mesai[s], 2)
    for saat in mesai_secenekleri:
        if abs(mesai_indeks[saat] - mevcut) > 2:
            prob += x[s, saat] == 0

# Sabit şirketler
for s in sabit_sirketler:
    prob += x[s, baslangic_mesai[s]] == 1

# Her saatte min 1 şirket
for saat in mesai_secenekleri:
    prob += lpSum(x[s, saat] for s in sirketler["isim"]) >= 1

# Şirket başına segment setleri
sirket_segmentler = {}
for s in sirketler["isim"]:
    segs = set()
    for _, r in rota_df[rota_df["sirket"] == s].iterrows():
        for kenar in r["kenarlar"]:
            segs.add((min(kenar[0], kenar[1]), max(kenar[0], kenar[1])))
    sirket_segmentler[s] = segs

# Çakışan çiftler için ceza
hedef = []
liste = sirketler["isim"].tolist()
for i in range(len(liste)):
    for j in range(i+1, len(liste)):
        s1, s2 = liste[i], liste[j]
        ortak = len(sirket_segmentler[s1] & sirket_segmentler[s2])
        if ortak > 5:
            for saat in mesai_secenekleri:
                ceza = LpVariable(f"ceza_{temiz(s1)}_{temiz(s2)}_{saat.replace(':','')}", lowBound=0)
                prob += ceza >= x[s1, saat] + x[s2, saat] - 1
                hedef.append(ortak * ceza)

prob += lpSum(hedef) if hedef else 0
prob.solve(PULP_CBC_CMD(msg=0))

# Sonuçları topla
yeni_mesai = {}
for s in sirketler["isim"]:
    for saat in mesai_secenekleri:
        if value(x[s, saat]) == 1:
            yeni_mesai[s] = saat

yeni_yuk            = segment_yuklerini_hesapla(yeni_mesai, rota_df)
yeni_skor, yeni_k   = cakisma_skoru_hesapla(yeni_yuk)
azalma              = (mevcut_skor - yeni_skor) / mevcut_skor * 100

# ── 6. SONUÇLAR ───────────────────────────────────────────────

print("\n" + "="*55)
print("OPTİMİZASYON SONUÇLARI")
print("="*55)
print(f"{'Şirket':<22} {'Eski':>6} → {'Yeni':>6}  Durum")
print("-"*55)
for s in sirketler["isim"]:
    e, y = baslangic_mesai[s], yeni_mesai[s]
    print(f"{s:<22} {e:>6} → {y:>6}  {'✅ Kaydırıldı' if e != y else '— Aynı'}")
print("="*55)
print(f"Çakışma skoru:  {mevcut_skor:,} → {yeni_skor:,}")
print(f"Azalma oranı:   %{azalma:.1f}")
print(f"Kritik segment: {len(mevcut_k)} → {len(yeni_k)}")

# ── 7. GÖRSELLEŞTIRME ─────────────────────────────────────────

KIRMIZI, YESIL, MAVI = "#E63946", "#4CAF50", "#2196F3"

fig = plt.figure(figsize=(16, 10))
fig.suptitle("İstanbul Trafik Optimizasyonu — Önce / Sonra", fontsize=15, fontweight="bold")
gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.4, wspace=0.35)

# Çakışma skoru
ax1 = fig.add_subplot(gs[0, 0])
bars = ax1.bar(["Önce","Sonra"], [mevcut_skor, yeni_skor], color=[KIRMIZI, YESIL], width=0.5, edgecolor="white")
ax1.set_title("Toplam Çakışma Skoru", fontweight="bold")
for bar, val in zip(bars, [mevcut_skor, yeni_skor]):
    ax1.text(bar.get_x()+bar.get_width()/2, bar.get_height()+200, f"{val:,}", ha="center", fontweight="bold")
ax1.annotate(f"↓ %{azalma:.1f}", xy=(0.5,0.92), xycoords="axes fraction", ha="center", color=YESIL, fontweight="bold")
ax1.set_ylim(0, mevcut_skor*1.15); ax1.spines[["top","right"]].set_visible(False)

# Kritik segment
ax2 = fig.add_subplot(gs[0, 1])
bars2 = ax2.bar(["Önce","Sonra"], [len(mevcut_k), len(yeni_k)], color=[KIRMIZI, YESIL], width=0.5, edgecolor="white")
ax2.set_title("Kritik Segment Sayısı", fontweight="bold")
for bar, val in zip(bars2, [len(mevcut_k), len(yeni_k)]):
    ax2.text(bar.get_x()+bar.get_width()/2, bar.get_height()+5, str(val), ha="center", fontweight="bold")
ax2.set_ylim(0, max(len(mevcut_k), len(yeni_k))*1.15); ax2.spines[["top","right"]].set_visible(False)

# Mesai dağılımı
ax3 = fig.add_subplot(gs[0, 2])
e_sayim = {s: sum(1 for si in sirketler["isim"] if baslangic_mesai[si]==s) for s in mesai_secenekleri}
y_sayim = {s: sum(1 for si in sirketler["isim"] if yeni_mesai[si]==s) for s in mesai_secenekleri}
xp = np.arange(len(mesai_secenekleri))
ax3.bar(xp-0.2, e_sayim.values(), 0.35, label="Önce", color=KIRMIZI, alpha=0.8)
ax3.bar(xp+0.2, y_sayim.values(), 0.35, label="Sonra", color=YESIL, alpha=0.8)
ax3.set_title("Mesai Dağılımı", fontweight="bold")
ax3.set_xticks(xp); ax3.set_xticklabels(mesai_secenekleri, rotation=45, fontsize=8)
ax3.legend(fontsize=9); ax3.spines[["top","right"]].set_visible(False)

# Saat bazlı yük
ax4 = fig.add_subplot(gs[1, :2])
yuk_e = {s: 0 for s in mesai_secenekleri}
yuk_y = {s: 0 for s in mesai_secenekleri}
for _, r in rota_df.iterrows():
    yuk_e[baslangic_mesai[r["sirket"]]] += r["calisan_sayisi"]
    yuk_y[yeni_mesai[r["sirket"]]]      += r["calisan_sayisi"]
xp2 = np.arange(len(mesai_secenekleri))
ax4.plot(xp2, list(yuk_e.values()), "o-", color=KIRMIZI, linewidth=2.5, markersize=8, label="Önce")
ax4.plot(xp2, list(yuk_y.values()), "o-", color=YESIL,   linewidth=2.5, markersize=8, label="Sonra")
ax4.fill_between(xp2, list(yuk_e.values()), list(yuk_y.values()), alpha=0.12, color=YESIL)
ax4.set_title("Saate Göre Yol Üzerindeki Çalışan Yükü", fontweight="bold")
ax4.set_xticks(xp2); ax4.set_xticklabels(mesai_secenekleri)
ax4.legend(); ax4.grid(alpha=0.3); ax4.spines[["top","right"]].set_visible(False)

# Tablo
ax5 = fig.add_subplot(gs[1, 2])
ax5.axis("off")
tablo_veri = [[s.replace(" A.Ş.","").replace(" Ltd.",""), baslangic_mesai[s], yeni_mesai[s],
               "✅" if baslangic_mesai[s] != yeni_mesai[s] else "—"]
              for s in sirketler["isim"]]
tablo = ax5.table(cellText=tablo_veri, colLabels=["Şirket","Eski","Yeni",""],
                  cellLoc="center", loc="center", bbox=[0,0,1,1])
tablo.auto_set_font_size(False); tablo.set_fontsize(8)
for (row, col), cell in tablo.get_celld().items():
    cell.set_edgecolor("#DDDDDD")
    if row == 0:
        cell.set_facecolor(MAVI); cell.set_text_props(color="white", fontweight="bold")
    elif row > 0 and tablo_veri[row-1][3] == "✅":
        cell.set_facecolor("#E8F5E9")
    else:
        cell.set_facecolor("#F9F9F9" if row%2==0 else "white")
ax5.set_title("Mesai Değişimleri", fontweight="bold")

plt.savefig("once_sonra_karsilastirma.png", dpi=150, bbox_inches="tight")
plt.show()
print("\nGrafik kaydedildi: once_sonra_karsilastirma.png")

# ── 8. ROTA HARİTASI ──────────────────────────────────────────

print("\nHarita oluşturuluyor...")
renkler_liste = ["#E63946","#2196F3","#4CAF50","#FF9800","#9C27B0",
                 "#00BCD4","#F44336","#3F51B5","#8BC34A","#FF5722",
                 "#607D8B","#E91E63","#009688","#FFC107","#795548"]
sirket_renk = {s: renkler_liste[i] for i, s in enumerate(sirketler["isim"])}

m = folium.Map(location=[41.01, 28.96], zoom_start=11, tiles="CartoDB positron")

for _, s in sirketler.iterrows():
    folium.CircleMarker([s["lat"], s["lon"]], radius=10,
        color=sirket_renk[s["isim"]], fill=True, fill_opacity=0.9,
        popup=folium.Popup(
            f"<b>{s['isim']}</b><br>Eski: {baslangic_mesai[s['isim']]}<br>Yeni: {yeni_mesai[s['isim']]}",
            max_width=220)
    ).add_to(m)

for _, rota in rota_df.iterrows():
    koordinatlar = []
    dugumler = [rota["kenarlar"][0][0]] + [k[1] for k in rota["kenarlar"]]
    for d in dugumler:
        if d in G.nodes:
            koordinatlar.append((G.nodes[d]["y"], G.nodes[d]["x"]))
    if koordinatlar:
        folium.PolyLine(koordinatlar, color=sirket_renk[rota["sirket"]],
            weight=2, opacity=0.5,
            tooltip=f"{rota['sirket']} | {rota['baslangic_ilce']} → {rota['mesafe_km']} km"
        ).add_to(m)

m.save("rota_haritasi.html")
print("Harita kaydedildi: rota_haritasi.html")
print("\nTamamlandı!")
