from fastapi import FastAPI
import requests
from bs4 import BeautifulSoup

app = FastAPI()

# --------- LISTA DE ESTACIONES ---------
@app.get("/xabia/lista")
def lista_estaciones():
    return {
        "estaciones": [
            {"id": "port", "nombre": "Xàbia - Port"},
            {"id": "lluca", "nombre": "Xàbia - Lluca"}
        ]
    }


# --------- ESTACIÓN PORT (tipo avamet.htm) ---------
@app.get("/xabia/port")
def datos_port():
    url = "https://www.meteoxabia.com/estacions/port/avamet.htm"
    datos = extraer_avamet(url)
    return {"estacion": "Port", "datos": datos}


# --------- ESTACIÓN LLUCA (tipo wx9.html) ---------
@app.get("/xabia/lluca")
def datos_lluca():
    url = "https://www.meteoxabia.com/estacions/lluca/wx9.html"
    datos = extraer_cumulus(url)
    return {"estacion": "Lluca", "datos": datos}


# --------- EXTRACTOR AVAMET ---------
def extraer_avamet(url):
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    datos = {}
    for linea in soup.find_all("span"):
        if ":" in linea.text:
            key, value = linea.text.split(":", 1)
            datos[key.strip()] = value.strip()

    return datos


# --------- EXTRACTOR CUMULUS (wx9.html) ---------
def extraer_cumulus(url):
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    datos = {}
    for tabla in soup.find_all("td"):
        if ":" in tabla.text:
            key, value = tabla.text.split(":", 1)
            datos[key.strip()] = value.strip()

    return datos


@app.get("/")
def root():
    return {"status": "API MeteoXabia online", "endpoints": ["/docs", "/xabia/lista"]}

