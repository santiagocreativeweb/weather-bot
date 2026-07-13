#!/usr/bin/env python3
# scripts/download_iem_obs.py — [NO EJECUTADO AQUÍ: red restringida. Correr en VPS.]
# Baja tmax diaria observada por estación desde IEM y arma data/obs.csv
# schema: station,date,tmax,tmax_int
# IEM tiene página específica "Wagering on ASOS Temperatures" — leerla antes de operar.
# [VERIFICAR-VIVO] endpoint y nombres de red (p.ej. red "GB__ASOS" para EGLC) contra
# https://mesonet.agron.iastate.edu/request/daily.phtml
import argparse, csv, math, sys
import requests

NETWORKS = {  # station -> red IEM  [VERIFICAR-VIVO]
    "KLGA": "NY_ASOS", "KORD": "IL_ASOS", "EGLC": "GB__ASOS",
    "LFPB": "FR__ASOS", "RJTT": "JP__ASOS", "RKSI": "KR__ASOS",
    "ZSPD": "CN__ASOS", "ZBAA": "CN__ASOS", "RCSS": "TW__ASOS",
    "LEMD": "ES__ASOS", "EDDM": "DE__ASOS", "LIMC": "IT__ASOS",
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="data/obs.csv")
    a = ap.parse_args()
    rows = []
    for st, net in NETWORKS.items():
        url = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
        p = dict(network=net, stations=st.lstrip("K") if st.startswith("K") else st,
                 var="max_temp_f", year1=a.start[:4], month1=a.start[5:7], day1=a.start[8:10],
                 year2=a.end[:4], month2=a.end[5:7], day2=a.end[8:10], format="csv")
        try:
            r = requests.get(url, params=p, timeout=120); r.raise_for_status()
        except Exception as e:
            print(f"[WARN] {st}: {e}", file=sys.stderr); continue
        lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
        hdr = lines[0].split(",")
        for l in lines[1:]:
            d = dict(zip(hdr, l.split(",")))
            v = d.get("max_temp_f")
            if not v or v in ("None", "M"): continue
            tf = float(v)
            # °C para estaciones no-US: convertir y REVISAR la cadena de redondeo WU [ASUNCION]
            val = tf if st.startswith("K") else (tf - 32) * 5 / 9
            # half-up EXPLICITO: round() de Python es banker's (26.5->26) y en buckets de 1°C
            # cambia el bucket ganador; la regla WU documentada en market.py es half-up
            rows.append([st, d.get("day"), round(val, 2), int(math.floor(val + 0.5))])
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["station", "date", "tmax", "tmax_int"])
        w.writerows(rows)
    print(f"escrito {a.out}: {len(rows)} filas")
    print("OJO: la resolución del mercado la define Weather Underground, no IEM. Validar")
    print("     obs IEM vs WU history en >=30 días por estación antes de confiar (deltas=riesgo).")

if __name__ == "__main__":
    main()
