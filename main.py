# main.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import requests
from bs4 import BeautifulSoup
import re
from typing import Dict, Any
import time

app = FastAPI(title="MeteoXabia API", version="1.0")

# -------------------------
# CONFIG: estaciones iniciales
# id debe ser corto y único
ESTACIONES = {
    "port": {
        "id": "port",
        "nombre": "Xàbia - Port",
        "url": "https://www.meteoxabia.com/estacions/port/avamet.htm",
        "tipo": "avamet"
    },
    "lluca": {
        "id": "lluca",
        "nombre": "Xàbia - Lluca/Rafalet",
        "url": "https://www.meteoxabia.com/estacions/lluca/wx9.html",
        "tipo": "wx9"
    },
    "faro": {
        "id": "faro",
        "nombre": "Xàbia - Faro Cabo de la Nao",
        "url": "https://www.meteoxabia.com/estacions/farolanao/wx9.html",
        "tipo": "wx9"
    },
}

# -------------------------
# CACHE simple en memoria para no hacer scrapes cada petición
CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 120  # segundos (ajusta según quieras; Render free: no abusar)

# -------------------------
# helpers de parsing
re_num = re.compile(r"[-+]?\d+(?:\.\d+)?")
def extract_number(s: str):
    """Devuelve primer número como float o None"""
    if not s:
        return None
    m = re_num.search(s.replace(",", "."))
    return float(m.group()) if m else None

def text_normalize(t: str):
    return t.strip().replace("\xa0", " ")

# parser genérico para avamet.htm (intenta encontrar pares "Label: value")
def parse_avamet(soup: BeautifulSoup) -> Dict[str, Any]:
    res = {}
    text_items = []
    # extraemos spans, td y p
    for tag in soup.find_all(["span", "td", "p", "div", "li"]):
        txt = text_normalize(tag.get_text(separator=" ", strip=True))
        if txt:
            text_items.append(txt)
    # Buscamos por patrones comunes
    combined = " | ".join(text_items)
    # temperatura
    m = re.search(r"([Tt]emp(?:erature)?|Temperatura|Temperatura:)[:\s]*([-+]?\d+(?:[\.,]\d+)?)\s*°?C", combined)
    if m:
        res["temperature"] = float(m.group(2).replace(",", "."))
    else:
        # fallback: buscar cualquier número con °C
        m2 = re.search(r"([-+]?\d+(?:[\.,]\d+)?)\s*°\s*C|([-+]?\d+(?:[\.,]\d+)?)\s*°C", combined)
        if m2:
            val = m2.group(1) or m2.group(2)
            res["temperature"] = float(val.replace(",", "."))
    # humedad
    m = re.search(r"(Hum|Humedad|Humidity)[:\s]*([-+]?\d+(?:[\.,]\d+)?)\s*%?", combined, re.I)
    if m:
        res["humidity"] = float(m.group(2).replace(",", "."))
    # viento: busco "Wind", "Viento" y velocidad (km/h or m/s)
    m = re.search(r"(Wind|Viento|Velocidad del viento)[:\s]*([-+]?\d+(?:[\.,]\d+)?)\s*(km/h|kph|m/s)?", combined, re.I)
    if m:
        val = float(m.group(2).replace(",", "."))
        unit = m.group(3) or "km/h"
        # si m/s convertir a km/h
        if unit and "m/s" in unit:
            val = val * 3.6
        res["wind_speed_kmh"] = round(val, 2)
    # lluvia: buscar 'Rain', 'Lluvia'
    m = re.search(r"(Rain|Lluvia).{0,15}?([-+]?\d+(?:[\.,]\d+)?)\s*(mm|l|litros)?", combined, re.I)
    if m:
        res["rain_mm"] = float(m.group(2).replace(",", "."))
    # max/min del día (search labels like Max Temp today / Min Temp)
    mmax = re.search(r"(Max(?:imum)? Temp(?:erature)?|Máx(?:ima)? temperatura|Temperatura máxima)[^\d\-+]*?([-+]?\d+(?:[\.,]\d+)?)", combined, re.I)
    mmin = re.search(r"(Min(?:imum)? Temp(?:erature)?|Mín(?:ima)? temperatura|Temperatura mínima)[^\d\-+]*?([-+]?\d+(?:[\.,]\d+)?)", combined, re.I)
    if mmax:
        res["day_temp_max"] = float(mmax.group(2).replace(",", "."))
    if mmin:
        res["day_temp_min"] = float(mmin.group(2).replace(",", "."))
    # monthly / yearly rainfall; busca 'Month' o 'Year'
    mmonth = re.search(r"(Month|Mes).{0,15}?([-+]?\d+(?:[\.,]\d+)?)\s*(mm|l)?", combined, re.I)
    myear = re.search(r"(Year|Año).{0,15}?([-+]?\d+(?:[\.,]\d+)?)\s*(mm|l)?", combined, re.I)
    if mmonth:
        res["month_rain_mm"] = float(mmonth.group(2).replace(",", "."))
    if myear:
        res["year_rain_mm"] = float(myear.group(2).replace(",", "."))
    # Si falta, intentamos extraer por pares label:value
    # buscaremos palabras clave
    keywords = {
        "pressure": ["presion", "pressure"],
        "wind_dir": ["dir", "direccion", "wind direction"],
        "rain_today": ["rain today", "lluvia hoy"],
    }
    # scanning for label: value patterns
    for t in text_items:
        if ":" in t:
            left, right = [x.strip() for x in t.split(":", 1)]
            low = left.lower()
            if any(k in low for k in ["hum", "humedad", "humidity"]):
                v = extract_number(right)
                if v is not None:
                    res["humidity"] = v
            if any(k in low for k in ["temp", "temperatura"]):
                v = extract_number(right)
                if v is not None and "temperature" not in res:
                    res["temperature"] = v
            if any(k in low for k in ["wind", "viento"]):
                v = extract_number(right)
                if v is not None and "wind_speed_kmh" not in res:
                    res["wind_speed_kmh"] = v
            if any(k in low for k in ["rain", "lluvia"]):
                v = extract_number(right)
                if v is not None and "rain_mm" not in res:
                    res["rain_mm"] = v
    return res

# parser para wx9.html (Cumulus / Weather Display alike)
def parse_wx9(soup: BeautifulSoup) -> Dict[str, Any]:
    res = {}
    # muchas plantillas wx9 muestran tablas con labels en <td>
    text_items = []
    for td in soup.find_all(["td", "span", "div", "p", "li"]):
        txt = text_normalize(td.get_text(" ", strip=True))
        if txt:
            text_items.append(txt)
    combined = " | ".join(text_items)
    # temperatura: buscar números con °C
    m = re.search(r"([-+]?\d+(?:[\.,]\d+)?)\s*°\s*C|([-+]?\d+(?:[\.,]\d+)?)\s*°C", combined)
    if m:
        val = m.group(1) or m.group(2)
        res["temperature"] = float(val.replace(",", "."))
    # humedad %
    m = re.search(r"Humidity[:\s]*([-+]?\d+(?:[\.,]\d+)?)\s*%", combined, re.I)
    if m:
        res["humidity"] = float(m.group(1).replace(",", "."))
    # wind
    m = re.search(r"Wind(?: Speed)?[:\s]*([-+]?\d+(?:[\.,]\d+)?)\s*(km/h|kph|mph|m/s)?", combined, re.I)
    if m:
        val = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "").lower()
        if "mph" in unit:
            val = val * 1.60934
        if "m/s" in unit:
            val = val * 3.6
        res["wind_speed_kmh"] = round(val, 2)
    # precipitation / rain
    m = re.search(r"(Rain|Precipitation|Precipitación|Lluvia).{0,20}?([-+]?\d+(?:[\.,]\d+)?)\s*(mm|l)?", combined, re.I)
    if m:
        res["rain_mm"] = float(m.group(2).replace(",", "."))
    # maxima/minima day/month/year (buscamos palabras clave)
    for pattern, key in [
        (r"Max(?:imum)? Temp(?:erature)?[^\d\-+]*?([-+]?\d+(?:[\.,]\d+)?)", "day_temp_max"),
        (r"Min(?:imum)? Temp(?:erature)?[^\d\-+]*?([-+]?\d+(?:[\.,]\d+)?)", "day_temp_min"),
        (r"Max Wind[^\d\-+]*?([-+]?\d+(?:[\.,]\d+)?)", "day_wind_max"),
    ]:
        m = re.search(pattern, combined, re.I)
        if m:
            res[key] = float(m.group(1).replace(",", "."))
    # fallback: key:value pairs
    for t in text_items:
        if ":" in t:
            left, right = [x.strip() for x in t.split(":", 1)]
            low = left.lower()
            if "humidity" in low or "humedad" in low:
                v = extract_number(right)
                if v is not None:
                    res["humidity"] = v
            if any(w in low for w in ["temp", "temperatura"]):
                v = extract_number(right)
                if v is not None and "temperature" not in res:
                    res["temperature"] = v
            if any(w in low for w in ["wind", "viento"]):
                v = extract_number(right)
                if v is not None and "wind_speed_kmh" not in res:
                    res["wind_speed_kmh"] = v
            if any(w in low for w in ["rain", "lluvia", "precip"]):
                v = extract_number(right)
                if v is not None and "rain_mm" not in res:
                    res["rain_mm"] = v
    return res

# -------------------------
# función para obtener datos normalizados
def scrape_station(station: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    sid = station["id"]
    # cache check
    cached = CACHE.get(sid)
    if cached and now - cached["ts"] < CACHE_TTL:
        return cached["data"]
    url = station["url"]
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        data = {"error": f"fetch_error: {str(e)}", "url": url}
        CACHE[sid] = {"ts": now, "data": data}
        return data
    soup = BeautifulSoup(r.content, "html.parser")
    tipo = station.get("tipo", "avamet")
    if tipo == "avamet":
        parsed = parse_avamet(soup)
    else:
        parsed = parse_wx9(soup)
    # normalizar salida
    out = {
        "id": sid,
        "nombre": station.get("nombre"),
        "url": url,
        "fetched_at": int(now),
        "datos": parsed
    }
    CACHE[sid] = {"ts": now, "data": out}
    return out

# -------------------------
# ENDPOINTS
@app.get("/api/estaciones")
def api_estaciones():
    lst = [{"id": v["id"], "nombre": v["nombre"]} for v in ESTACIONES.values()]
    return JSONResponse(content=lst)

@app.get("/api/estacion/{station_id}/completo")
def api_estacion_completo(station_id: str):
    s = ESTACIONES.get(station_id)
    if not s:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    data = scrape_station(s)
    return JSONResponse(content=data)

@app.get("/api/estacion/{station_id}/ahora")
def api_estacion_ahora(station_id: str):
    obj = api_estacion_completo.__wrapped__(station_id) if hasattr(api_estacion_completo, "__wrapped__") else api_estacion_completo(station_id)
    # object's "datos" contains us
    datos = obj.get("datos", {})
    ahora = {
        "temperature": datos.get("temperature"),
        "humidity": datos.get("humidity"),
        "wind_kmh": datos.get("wind_speed_kmh"),
        "rain_mm": datos.get("rain_mm"),
        "fetched_at": obj.get("fetched_at"),
    }
    return JSONResponse(content=ahora)

@app.get("/api/estacion/{station_id}/dia")
def api_estacion_dia(station_id: str):
    s = ESTACIONES.get(station_id)
    if not s:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    data = scrape_station(s)
    datos = data.get("datos", {})
    dia = {
        "day_max_temp": datos.get("day_temp_max"),
        "day_min_temp": datos.get("day_temp_min"),
        "day_max_wind": datos.get("day_wind_max"),
        "rain_today": datos.get("rain_today") or datos.get("rain_mm"),
        "fetched_at": data.get("fetched_at")
    }
    return JSONResponse(content=dia)

@app.get("/api/estacion/{station_id}/mes")
def api_estacion_mes(station_id: str):
    s = ESTACIONES.get(station_id)
    if not s:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    data = scrape_station(s)
    datos = data.get("datos", {})
    mes = {
        "month_rain_mm": datos.get("month_rain_mm"),
        "month_max_temp": datos.get("month_temp_max"),
        "month_min_temp": datos.get("month_temp_min"),
        "fetched_at": data.get("fetched_at")
    }
    return JSONResponse(content=mes)

@app.get("/api/estacion/{station_id}/anio")
def api_estacion_anio(station_id: str):
    s = ESTACIONES.get(station_id)
    if not s:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    data = scrape_station(s)
    datos = data.get("datos", {})
    anio = {
        "year_rain_mm": datos.get("year_rain_mm"),
        "year_max_temp": datos.get("year_temp_max"),
        "year_min_temp": datos.get("year_temp_min"),
        "fetched_at": data.get("fetched_at")
    }
    return JSONResponse(content=anio)

# -------------------------
# util: añadir nueva estación (runtime)
@app.post("/api/estacion/add")
def api_add_station(item: Dict[str, Any]):
    # item must have id, nombre, url, tipo
    if not all(k in item for k in ("id", "nombre", "url")):
        raise HTTPException(status_code=400, detail="Faltan campos id/nombre/url")
    sid = item["id"]
    ESTACIONES[sid] = {
        "id": sid,
        "nombre": item["nombre"],
        "url": item["url"],
        "tipo": item.get("tipo", "avamet")
    }
    return JSONResponse(content={"ok": True, "estacion": ESTACIONES[sid]})
