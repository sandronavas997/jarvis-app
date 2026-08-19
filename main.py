import os
import re
import tempfile
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
from google import genai
from google.genai import types
import edge_tts

app = FastAPI()

# Configuración de claves y autenticación
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
gemini_api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

# Modelo Pydantic para envíos directos desde NinjaTrader 8
class NinjaTraderPayload(BaseModel):
    clave: str
    symbol: str
    price: float
    vwap: Optional[float] = None
    poc: Optional[float] = None
    delta: Optional[float] = None
    volume: Optional[float] = None
    context_note: Optional[str] = ""
    modelo: Optional[str] = "flash"

def limpiar_texto_para_voz(texto: str) -> str:
    texto = re.sub(r'[\*\#\_\`\~\>]', '', texto)
    texto = re.sub(r'[\U00010000-\U0010ffff]', '', texto)
    return texto.strip()

SYSTEM_INSTRUCTION = (
    "Eres JARVIS, el copiloto y analista experto de trading en mercados de futuros ($MNQ, $MES, $NQ, $ES). "
    "Tu especialidad principal es el análisis de Order Flow (Delta, Footprint, DOM), Volume Profile, Market Profile "
    "y la Teoría de la Subasta (Auction Market Theory).\n\n"
    "REGLAS Y METODOLOGÍA DE ANÁLISIS:\n"
    "1. Contexto y Perfil de Volumen: Analiza la ubicación del precio respecto a las Zonas de Valor (VAH, VAL), "
    "el POC (Point of Control) y la VWAP. Determina si el mercado está en equilibrio (rotacional) o desequilibrio (tendencial).\n"
    "2. Flujo de Órdenes y Volumen: Evalúa señales de absorción, agotamiento, clusters de volumen y divergencias en el Delta.\n"
    "3. Confirmación Multi-Timeframe (HTF): Cruza la información recibida (captura o métricas) con la estructura macro. "
    "No recomiendes entradas en contra de la tendencia/zona HTF a menos que exista una absorción clara y confirmada.\n"
    "4. Estrategia y Plan de Acción:\n"
    "   - Si se evalúa una entrada potencial, proporciona:\n"
    "     a) DIAGNÓSTICO DEL MERCADO: Estado estructural actual.\n"
    "     b) EVALUACIÓN DE SEÑAL: ¿Hay confirmación para entrar? (Entrada Long / Entrada Short / Esperar Re-test).\n"
    "     c) PARÁMETROS TÉCNICOS: Zona de Entrada sugerida, Nivel de Invalidación (Stop Loss técnico) y Objetivos (Take Profit / VWAP / POC).\n"
    "   - Si la posición está activa, indica si mantener, ajustar trailing stop a VWAP/POC o CERRAR la posición inmediatamente por cambio en la subasta.\n\n"
    "Mantén un tono profesional, técnico, sintético y conciso. Dirígete al usuario siempre como Señor."
)

@app.get("/")
async def read_root():
    if os.path.exists("templates/index.html"):
        return FileResponse("templates/index.html")
    return HTMLResponse("<h1>Error: No se encontró el archivo index.html en templates/</h1>")

# Endpoint para la interfaz Web (Capturas + Voz + Texto)
@app.post("/api/chat")
async def chat(
    clave: str = Form(""),
    mensaje_texto: str = Form(None),
    image_file: UploadFile = File(None),
    modelo: str = Form("flash")
):
    if ACCESS_CODE and clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")

    if not client:
        return {"texto": "Error: La variable GEMINI_API_KEY no está configurada en Render."}

    model_name = "gemini-3.6-flash" if modelo == "flash" else "gemini-3.6-pro"

    contents = []
    if image_file and image_file.filename:
        image_bytes = await image_file.read()
        mime_type = image_file.content_type or "image/jpeg"
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    prompt = mensaje_texto or "Analice la captura adjunta y evalúe la estructura del mercado bajo la metodología de Order Flow."
    contents.append(prompt)

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
        )
        
        texto_respuesta = response.text or "Sin respuesta."
        texto_voz = limpiar_texto_para_voz(texto_respuesta)

        has_audio = False
        if texto_voz:
            temp_dir = tempfile.gettempdir()
            audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
            communicate = edge_tts.Communicate(texto_voz, "es-ES-AlvaroNeural")
            await communicate.save(audio_path)
            has_audio = True

        return {"texto": texto_respuesta, "has_audio": has_audio}

    except Exception as e:
        return {"texto": f"Error al procesar la consulta: {str(e)}"}

# Endpoint exclusivo para la integración directa desde NinjaTrader 8
@app.post("/api/ninjatrader")
async def ninjatrader_feed(payload: NinjaTraderPayload):
    if ACCESS_CODE and payload.clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")

    if not client:
        return {"texto": "Error: GEMINI_API_KEY no configurada en el servidor."}

    model_name = "gemini-3.6-flash" if payload.modelo == "flash" else "gemini-3.6-pro"

    prompt_nt = (
        f"DATOS DE MERCADO EN TIEMPO REAL DESDE NINJATRADER 8:\n"
        f"- Activo: {payload.symbol}\n"
        f"- Precio Actual: {payload.price}\n"
        f"- VWAP: {payload.vwap if payload.vwap is not None else 'N/A'}\n"
        f"- POC: {payload.poc if payload.poc is not None else 'N/A'}\n"
        f"- Delta Acumulado: {payload.delta if payload.delta is not None else 'N/A'}\n"
        f"- Volumen: {payload.volume if payload.volume is not None else 'N/A'}\n"
        f"- Nota / Contexto Adicional: {payload.context_note}\n\n"
        f"Diagnostique el mercado actual y recomiende el plan de acción (Entrada/Cierre/Trailing Stop)."
    )

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt_nt],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
        )
        
        texto_respuesta = response.text or "Sin respuesta."
        texto_voz = limpiar_texto_para_voz(texto_respuesta)

        has_audio = False
        if texto_voz:
            temp_dir = tempfile.gettempdir()
            audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
            communicate = edge_tts.Communicate(texto_voz, "es-ES-AlvaroNeural")
            await communicate.save(audio_path)
            has_audio = True

        return {"texto": texto_respuesta, "has_audio": has_audio}

    except Exception as e:
        return {"texto": f"Error: {str(e)}"}

@app.get("/api/audio")
async def get_audio():
    temp_dir = tempfile.gettempdir()
    audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
    if os.path.exists(audio_path):
        return FileResponse(audio_path, media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Audio no disponible")
