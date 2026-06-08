# =============================================================
# trafik_model.py  —  Sıkışıklık-Duyarlı Trafik Atama Motoru
# -------------------------------------------------------------
# İstanbul Trafik Optimizasyonu | Bitirme Projesi
#
# AMAÇ:
#   Mevcut modelde İBB saatlik hızları "sabit" varsayılıyordu; yani
#   bir saate ne kadar araç yığılırsa yığılsın o saatin hızı değişmiyordu.
#   Bu modül bu eksiği gideriyor:
#
#   1) İBB saatlik hızı = ARKA PLAN (ambient) hızı  ->  v0(hücre, saat)
#   2) Bizim şirket çalışanlarımız = KONTROL EDİLEBİLİR EK YÜK
#   3) Volume-Delay (BPR tipi) fonksiyon:
#         v(hücre,saat) = v0 / (1 + alfa * (N/C)^beta)
#      N: o hücre-saatte bizim eklediğimiz araç sayısı
#      C: hücre kapasitesi, alfa/beta: BPR katsayıları
#   4) Süre, rotanın geçtiği TÜM hücrelerin toplamı olarak hesaplanır
#      (sadece orta nokta değil).
#   5) Atama ile hızlar birbirini etkilediği için problem doğrusal
#      değildir -> Method of Successive Averages (MSA) ile iteratif
#      DENGE (fixed-point) çözülür. Her iterasyonda MILP doğrusal kalır.
#
# Bu sayede:
#   - Yeni yığılmalar artık hedefte CEZALANDIRILIR (yığılınca o saatin
#     hızı düşer, herkesin süresi artar -> optimizer kaçınır).
#   - Raporlanan "yeni süre" sıkışmış (gerçekçi) hızla hesaplanır;
#     yığılma azalan yerlerde hız artar, süre düşer -> bu da yakalanır.
# =============================================================

from math import radians, sin, cos, sqrt, atan2
from collections import defaultdict

GRID = 0.02  # İBB ızgara adımı (~2 km)


# ── 1. COĞRAFİ YARDIMCILAR ────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    """İki nokta arası kuş uçuşu mesafe (km)."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


class HizModeli:
    """İBB hız tablosunu sarmalayan; hücre eşleme + sıkışıklık hesabı yapan sınıf."""

    def __init__(self, ibb_hiz_tablosu, alfa=0.50, beta=2.0,
                 kapasite=None, doluluk=1.5, carpan_max=2.5,
                 tolerans=1.5, varsayilan_hiz=30.0):
        # tablo: {(lat,lon): {saat:int -> hız:float}}
        self.tablo = {}
        for k, v in ibb_hiz_tablosu.items():
            try:
                lat, lon = float(k[0]), float(k[1])
            except Exception:
                continue
            saatlik = {}
            for sa, hz in v.items():
                try:
                    saatlik[int(sa)] = float(hz)
                except Exception:
                    pass
            self.tablo[(lat, lon)] = saatlik
        self.hucreler = list(self.tablo.keys())

        # Volume-delay parametreleri
        self.alfa = float(alfa)            # BPR alfa (sıkışıklık şiddeti)
        self.beta = float(beta)            # BPR beta (üs) — yumuşak: 2
        self.doluluk = float(doluluk)      # araç başına kişi (carpool/servis)
        self.carpan_max = float(carpan_max)  # süre çarpanı ÜST SINIRI (patlamayı önler)
        self.tolerans = float(tolerans)    # kapasite = tolerans * referans yük
        self.varsayilan_hiz = float(varsayilan_hiz)
        # kapasite: verilmezse veriden otomatik kalibre edilir (kalibre_et)
        self.kapasite = float(kapasite) if kapasite else None

        self._yakin_cache = {}             # (lat,lon) -> en yakın hücre anahtarı

    def kalibre_et(self, yuk_dict):
        """
        Kapasiteyi VERİYE göre otomatik belirle: mevcut atamadaki hücre-saat
        yüklerinin (sıfır olmayanların) 70. yüzdeliğini 'referans' kabul et,
        C = tolerans * referans. Böylece N/C ~ O(1) kalır, büyük veride patlamaz.
        """
        yukler = sorted(v for v in yuk_dict.values() if v > 0)
        if not yukler:
            self.kapasite = 1.0
            return self.kapasite
        # 70. yüzdelik
        idx = int(0.70 * (len(yukler) - 1))
        referans = yukler[idx]
        self.kapasite = max(1.0, self.tolerans * referans)
        return self.kapasite

    # -- hücre eşleme --
    def _snap(self, lat, lon):
        return (round(round(lat / GRID) * GRID, 2),
                round(round(lon / GRID) * GRID, 2))

    def en_yakin_hucre(self, lat, lon):
        """Bir noktayı en yakın İBB hücresine eşle (cache'li)."""
        snap = self._snap(lat, lon)
        if snap in self.tablo:
            return snap
        if snap in self._yakin_cache:
            return self._yakin_cache[snap]
        # ızgara hücresi tabloda yoksa en yakın mevcut hücreyi bul
        en_iyi, en_mesafe = None, float("inf")
        for h in self.hucreler:
            d = abs(h[0] - lat) + abs(h[1] - lon)  # manhattan yeterli
            if d < en_mesafe:
                en_mesafe, en_iyi = d, h
        self._yakin_cache[snap] = en_iyi
        return en_iyi

    def v0(self, hucre, saat_int):
        """Arka plan (sıkışıklıksız) hız — İBB tarihsel ortalaması."""
        d = self.tablo.get(hucre, {})
        return d.get(saat_int, self.varsayilan_hiz)

    # -- volume-delay --
    def gecikme_carpani(self, yuk):
        """
        Eklenen araç yükü -> süre çarpanı.
        carpan = 1 + alfa*(N/C)^beta, fakat carpan_max ile ÜSTTEN SINIRLI.
        Üst sınır, büyük yüklerde sürenin sonsuza gitmesini engeller
        (gerçekte de bir yol tamamen kilitlense bile süre sonsuz olmaz).
        """
        if yuk <= 0 or not self.kapasite:
            return 1.0
        carpan = 1.0 + self.alfa * (yuk / self.kapasite) ** self.beta
        return min(self.carpan_max, carpan)

    def sikismis_hiz(self, hucre, saat_int, yuk):
        return self.v0(hucre, saat_int) / self.gecikme_carpani(yuk)


# ── 2. ROTA -> HÜCRE PARÇALAMA ───────────────────────────────

def rota_hucreleri(route_coords, model):
    """
    Rota poligon çizgisini İBB hücrelerine böler.
    Dönüş: {hücre_anahtarı: o hücredeki uzunluk_km}
    route_coords: [[lat,lon], [lat,lon], ...]
    """
    parcalar = defaultdict(float)
    if not route_coords or len(route_coords) < 2:
        return parcalar
    for i in range(len(route_coords) - 1):
        lat1, lon1 = route_coords[i]
        lat2, lon2 = route_coords[i + 1]
        km = haversine_km(lat1, lon1, lat2, lon2)
        if km <= 0:
            continue
        mlat, mlon = (lat1 + lat2) / 2, (lon1 + lon2) / 2  # segment orta noktası
        hucre = model.en_yakin_hucre(mlat, mlon)
        parcalar[hucre] += km
    return parcalar


# ── 3. YÜK VE SÜRE HESABI ────────────────────────────────────

class RotaParcalari:
    """Her güzergah için: şirket, kişi sayısı, araç sayısı ve hücre parçaları."""

    def __init__(self, guzergah_df, model):
        self.model = model
        self.rotalar = []  # list of dict
        for idx, g in guzergah_df.iterrows():
            coords = g.get("route_coords", None)
            if coords is None or (hasattr(coords, "__len__") and len(coords) == 0):
                # geometri yoksa başlangıç-şirket düz çizgisini örnekle
                coords = _duz_cizgi(
                    float(g["baslangic_lat"]), float(g["baslangic_lon"]),
                    float(g.get("sirket_lat", g["baslangic_lat"])),
                    float(g.get("sirket_lon", g["baslangic_lon"])),
                )
            kisi = int(g["calisan_sayisi"])
            self.rotalar.append({
                "idx": idx,
                "sirket": str(g["sirket"]),
                "ilce": str(g["baslangic_ilce"]),
                "kisi": kisi,
                "arac": kisi / model.doluluk,
                "hucreler": dict(rota_hucreleri(list(coords), model)),
            })

    def yukleri_hesapla(self, mesai_dict):
        """Atamaya göre her (hücre, saat) için toplam eklenen araç yükü."""
        yuk = defaultdict(float)
        for r in self.rotalar:
            saat = mesai_dict.get(r["sirket"])
            if saat is None:
                continue
            s = int(saat.split(":")[0])
            for hucre, km in r["hucreler"].items():
                yuk[(hucre, s)] += r["arac"]
        return yuk

    def rota_suresi(self, rota, saat_int, yuk):
        """Bir rotanın belirli saatte, verilen yük altında süresi (dk)."""
        toplam_dk = 0.0
        for hucre, km in rota["hucreler"].items():
            n = yuk.get((hucre, saat_int), 0.0)
            hiz = self.model.sikismis_hiz(hucre, saat_int, n)
            if hiz <= 0:
                hiz = self.model.varsayilan_hiz
            toplam_dk += (km / hiz) * 60.0
        return toplam_dk

    def ortalama_sure(self, mesai_dict):
        """Atamanın denge yükü altında kişi-ağırlıklı ortalama süresi (dk)."""
        yuk = self.yukleri_hesapla(mesai_dict)
        tsure, tkisi = 0.0, 0
        detay = []
        for r in self.rotalar:
            saat = mesai_dict.get(r["sirket"], "08:00")
            s = int(saat.split(":")[0])
            sure = self.rota_suresi(r, s, yuk)
            tsure += sure * r["kisi"]
            tkisi += r["kisi"]
            detay.append({
                "sirket": r["sirket"], "ilce": r["ilce"],
                "saat": saat, "sure_dk": round(sure, 1), "kisi": r["kisi"],
            })
        ort = tsure / tkisi if tkisi else 0.0
        return round(ort, 1), detay


def _duz_cizgi(lat1, lon1, lat2, lon2, n=12):
    return [[lat1 + (lat2 - lat1) * t / n, lon1 + (lon2 - lon1) * t / n]
            for t in range(n + 1)]


# ── 4. MILP KATSAYI ÜRETİMİ (marjinal-tutarlı) ───────────────

def sure_katsayilari(rp, mesai_dict, saat_secenekleri):
    """
    Her (rota_idx, saat) için süre katsayısı üretir.
    Marjinal-tutarlı: diğer şirketler MEVCUT atamada sabitken,
    bu rotayı 'saat'e koyarsak yaşayacağı süre.
    Böylece MILP "ben buraya gelirsem karşılaşacağım sıkışıklığı" görür.
    """
    # taban yük: tüm rotalar mevcut atamada
    taban = rp.yukleri_hesapla(mesai_dict)
    sonuc = {}
    for r in rp.rotalar:
        mevcut_s = int(mesai_dict.get(r["sirket"], "08:00").split(":")[0])
        for saat in saat_secenekleri:
            s = int(saat.split(":")[0])
            # bu rotayı mevcut saatinden çıkar, aday saate ekle
            for hucre, km in r["hucreler"].items():
                taban[(hucre, mevcut_s)] -= r["arac"]
                taban[(hucre, s)] += r["arac"]
            sonuc[(r["idx"], saat)] = rp.rota_suresi(r, s, taban)
            # geri al
            for hucre, km in r["hucreler"].items():
                taban[(hucre, mevcut_s)] += r["arac"]
                taban[(hucre, s)] -= r["arac"]
    return sonuc


# ── 5. İTERATİF DENGE (MSA) ───────────────────────────────────

def denge_dongusu(rp, mesai_dict, saat_secenekleri, milp_coz,
                  max_iter=15, msa=True, log=None):
    """
    Fixed-point denge: MILP'i tekrar tekrar çöz, her seferinde
    yükleri (MSA ile yumuşatarak) güncelle. Atama sabitlenince dur.

    milp_coz(katsayilar) -> {sirket: saat}  (MILP'i çözen callback)
    Dönüş: (son_mesai_dict, gecmis_list)
    """
    mevcut = dict(mesai_dict)         # başlangıç: kullanıcının mevcut mesaileri
    yumusak_yuk = rp.yukleri_hesapla(mevcut)
    gecmis = []

    for it in range(1, max_iter + 1):
        # mevcut yumuşatılmış yüke göre katsayı üret
        katsayilar = _katsayi_yumusak(rp, mevcut, yumusak_yuk, saat_secenekleri)
        yeni = milp_coz(katsayilar)
        if not yeni:
            break

        yeni_yuk = rp.yukleri_hesapla(yeni)
        # MSA: lambda = 1/it ile yükleri yumuşat (salınımı engeller)
        lam = (1.0 / it) if msa else 1.0
        anahtarlar = set(yumusak_yuk) | set(yeni_yuk)
        yumusak_yuk = {k: (1 - lam) * yumusak_yuk.get(k, 0.0)
                          + lam * yeni_yuk.get(k, 0.0)
                       for k in anahtarlar}

        ort, _ = rp.ortalama_sure(yeni)
        degisim = sum(1 for s in yeni if yeni.get(s) != mevcut.get(s))
        gecmis.append({"iter": it, "ort_sure": ort, "degisen": degisim})
        if log:
            log(f"  iter {it}: ort_sure={ort} dk, değişen atama={degisim}")

        sabitlendi = (yeni == mevcut)
        mevcut = yeni
        if sabitlendi and it > 1:
            break

    return mevcut, gecmis


def _katsayi_yumusak(rp, mesai_dict, yumusak_yuk, saat_secenekleri):
    """sure_katsayilari'nın MSA-yumuşatılmış yük versiyonu."""
    taban = defaultdict(float, dict(yumusak_yuk))
    sonuc = {}
    for r in rp.rotalar:
        mevcut_s = int(mesai_dict.get(r["sirket"], "08:00").split(":")[0])
        for saat in saat_secenekleri:
            s = int(saat.split(":")[0])
            for hucre, km in r["hucreler"].items():
                taban[(hucre, mevcut_s)] -= r["arac"]
                taban[(hucre, s)] += r["arac"]
            sonuc[(r["idx"], saat)] = rp.rota_suresi(r, s, taban)
            for hucre, km in r["hucreler"].items():
                taban[(hucre, mevcut_s)] += r["arac"]
                taban[(hucre, s)] -= r["arac"]
    return sonuc


# ── 6. ÜST-SEVİYE: DENGE OPTİMİZASYONU + RAPOR ───────────────

def calistir_denge_optimizasyon(sirketler_df, guzergah_df, mesai_secenekleri,
                                 max_sapma, max_saatlik_oran, mod,
                                 alfa=0.50, beta=2.0, tolerans=1.5, carpan_max=2.5,
                                 doluluk=1.5, max_iter=15, log=None):
    """
    app.py'nin çağırdığı tek fonksiyon. Sıkışıklık-duyarlı denge
    optimizasyonunu çalıştırır ve TÜM raporu (önce/sonra, sıkışıklık dahil)
    döndürür.

    Kapasite VERİDEN otomatik kalibre edilir (mevcut atamadaki yüklerin
    referansına göre) ve önce/sonra için SABİT tutulur; yavaşlama carpan_max
    ile sınırlıdır. Böylece süreler gerçekçi kalır (binlerce dk olmaz).
    """
    from pulp import (LpProblem, LpMinimize, LpVariable, lpSum, value,
                      PULP_CBC_CMD, LpStatus)
    import pandas as pd

    mesai_indeks = {s: i for i, s in enumerate(mesai_secenekleri)}
    isimler   = [str(r["isim"]) for _, r in sirketler_df.iterrows()]
    mesai_dict = {str(r["isim"]): str(r["mevcut_mesai"]) for _, r in sirketler_df.iterrows()}
    sabit_list = [str(r["isim"]) for _, r in sirketler_df.iterrows() if r["sabit"]]
    kisi_top   = {s: int(guzergah_df[guzergah_df["sirket"] == s]["calisan_sayisi"].sum())
                  for s in isimler}
    toplam_cal = int(guzergah_df["calisan_sayisi"].sum())

    # İBB hız tablosunu yükle
    try:
        from ibb_hiz_tablosu import IBB_HIZ_TABLOSU
    except Exception:
        IBB_HIZ_TABLOSU = {}

    model = HizModeli(IBB_HIZ_TABLOSU, alfa=alfa, beta=beta, kapasite=None,
                      doluluk=doluluk, carpan_max=carpan_max, tolerans=tolerans)
    rp = RotaParcalari(guzergah_df, model)

    # KAPASİTE KALİBRASYONU: mevcut (önce) atamadaki yüklerden bir kez belirle,
    # önce/sonra ve tüm iterasyonlar için SABİT tut.
    mevcut_yuk = rp.yukleri_hesapla(mesai_dict)
    kalibre_C = model.kalibre_et(mevcut_yuk)
    if log:
        log(f"  kalibre kapasite C = {kalibre_C:.1f} araç/hücre-saat "
            f"(tolerans={tolerans}, α={alfa}, β={beta}, maks yavaşlama={carpan_max}x)")

    # uzun_sure modu için mevcut (sıkışıklık dahil) süre ağırlıkları
    mevcut_sure_route = {}
    for r in rp.rotalar:
        s = int(mesai_dict.get(r["sirket"], "08:00").split(":")[0])
        mevcut_sure_route[r["idx"]] = rp.rota_suresi(r, s, mevcut_yuk)
    ort_ref = (sum(mevcut_sure_route.values()) / len(mevcut_sure_route)
               if mevcut_sure_route else 1.0)

    tepe_saatler = {"08:00", "08:30", "09:00"}
    temiz = lambda s: "".join(c if c.isalnum() else "_" for c in str(s))

    def milp_coz(katsayilar):
        prob = LpProblem("Denge_Opt", LpMinimize)
        x = {(s, sa): LpVariable(f"x_{temiz(s)}_{sa.replace(':','')}", cat="Binary")
             for s in isimler for sa in mesai_secenekleri}
        # her şirket 1 saat
        for s in isimler:
            prob += lpSum(x[s, sa] for sa in mesai_secenekleri) == 1
        # max sapma
        for s in isimler:
            mv = mesai_indeks.get(mesai_dict.get(s, "08:00"), 2)
            for sa in mesai_secenekleri:
                if abs(mesai_indeks[sa] - mv) > max_sapma:
                    prob += x[s, sa] == 0
        # sabit şirketler
        for s in sabit_list:
            m = mesai_dict.get(s, "08:00")
            if m in mesai_secenekleri:
                prob += x[s, m] == 1
        # saatlik kapasite
        for sa in mesai_secenekleri:
            prob += lpSum(x[s, sa] * kisi_top[s] for s in isimler) <= toplam_cal * max_saatlik_oran
        # hedef (moda göre, sıkışıklık-duyarlı katsayılarla)
        hedef = []
        for r in rp.rotalar:
            s = r["sirket"]
            for sa in mesai_secenekleri:
                sure = katsayilar[(r["idx"], sa)]
                if mod == "uzun_sure":
                    agirlik = max(0.5, mevcut_sure_route[r["idx"]] / ort_ref) if ort_ref else 1.0
                    hedef.append(x[s, sa] * r["kisi"] * sure * agirlik)
                elif mod == "peak_yuk":
                    ceza = 1000 if sa in tepe_saatler else 0
                    hedef.append(x[s, sa] * r["kisi"] * (ceza + sure))
                else:  # ortalama_sure
                    hedef.append(x[s, sa] * r["kisi"] * sure)
        prob += lpSum(hedef)
        prob.solve(PULP_CBC_CMD(msg=0))
        if LpStatus[prob.status] != "Optimal":
            return {}
        return {s: sa for s in isimler for sa in mesai_secenekleri
                if value(x[s, sa]) == 1}

    # iteratif denge
    yeni_mesai, gecmis = denge_dongusu(rp, mesai_dict, mesai_secenekleri,
                                       milp_coz, max_iter=max_iter, msa=True, log=log)
    status = "Optimal" if yeni_mesai else "Infeasible"
    if not yeni_mesai:
        yeni_mesai = dict(mesai_dict)

    # önce/sonra raporları — İKİSİ DE sıkışıklık dahil
    ort_eski, detay_eski = _detayli_rapor(rp, mesai_dict)
    ort_yeni, detay_yeni = _detayli_rapor(rp, yeni_mesai)

    # harita için rota bazlı süreler
    yuk_eski = rp.yukleri_hesapla(mesai_dict)
    yuk_yeni = rp.yukleri_hesapla(yeni_mesai)
    rota_sure_eski, rota_sure_yeni = {}, {}
    for r in rp.rotalar:
        se = int(mesai_dict.get(r["sirket"], "08:00").split(":")[0])
        sy = int(yeni_mesai.get(r["sirket"], "08:00").split(":")[0])
        rota_sure_eski[r["idx"]] = round(rp.rota_suresi(r, se, yuk_eski), 1)
        rota_sure_yeni[r["idx"]] = round(rp.rota_suresi(r, sy, yuk_yeni), 1)

    return {
        "yeni_mesai": yeni_mesai, "status": status, "gecmis": gecmis,
        "ort_eski": ort_eski, "detay_eski": detay_eski,
        "ort_yeni": ort_yeni, "detay_yeni": detay_yeni,
        "rota_sure_eski": rota_sure_eski, "rota_sure_yeni": rota_sure_yeni,
    }


def _detayli_rapor(rp, mesai_dict):
    """app.py'nin beklediği kolonlarla detay tablosu (sıkışıklık dahil)."""
    import pandas as pd
    yuk = rp.yukleri_hesapla(mesai_dict)
    tsure, tkisi, satirlar = 0.0, 0, []
    for r in rp.rotalar:
        saat = mesai_dict.get(r["sirket"], "08:00")
        s = int(saat.split(":")[0])
        sure = rp.rota_suresi(r, s, yuk)
        km = sum(r["hucreler"].values())
        ort_hiz = (km / (sure / 60.0)) if sure > 0 else 0.0
        tsure += sure * r["kisi"]; tkisi += r["kisi"]
        satirlar.append({
            "Şirket": r["sirket"], "İlçe": r["ilce"], "Mesai": saat,
            "Bölge Hızı (km/h)": round(ort_hiz, 1),
            "Tahmini Süre (dk)": round(sure, 1), "Çalışan": r["kisi"],
        })
    ort = round(tsure / tkisi, 1) if tkisi else 0.0
    return ort, pd.DataFrame(satirlar)
