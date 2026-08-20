import os
import re
import json
import asyncio
import tempfile
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
import websockets
import edge_tts
from google import genai
from google.genai import types

app = FastAPI(title="JARVIS Copilot - Unified Edition v5")
templates = Jinja2Templates(directory="templates")

# Configuración de seguridad y credenciales de Render
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Memoria global para almacenar las últimas métricas de NinjaTrader 8
latest_nt_data = {}

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

# URL de la API Multimodal Live de Gemini (v1alpha)
GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"

def limpiar_texto_para_voz(texto: str) -> str:
    # Eliminar marcas de formato markdown comunes para que la voz de edge-tts sea natural
    texto = re.sub(r'[\*\#\_\`\~\>]', '', texto)
    texto = re.sub(r'[\U00010000-\U0010ffff]', '', texto)  # Eliminar emojis
    return texto.strip()

@app.get("/")
async def read_root(request: Request):
    if os.path.exists("templates/index.html"):
        return templates.TemplateResponse("index.html", {"request": request})
    return HTMLResponse("<h1>Error: No se encontró el archivo index.html en templates/</h1>")

# Endpoint para recibir datos silenciosos de NinjaTrader 8 (C#)
@app.post("/api/ninjatrader")
async def ninjatrader_feed(payload: NinjaTraderPayload):
    global latest_nt_data
    if ACCESS_CODE and payload.clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")
    
    latest_nt_data = {
        "symbol": payload.symbol,
        "price": payload.price,
        "vwap": payload.vwap,
        "poc": payload.poc,
        "delta": payload.delta,
        "volume": payload.volume,
        "context_note": payload.context_note
    }
    print(f"[NINJATRADER 8 FEED] Datos actualizados: {latest_nt_data}")
    return {"status": "success", "message": "Métricas de mercado actualizadas en JARVIS"}

# Endpoint para el Chat Híbrido Tradicional (Texto, Imagen, Grabación de Audio desde navegador)
@app.post("/api/chat")
async def chat_endpoint(
    clave: str = Form(""),
    mensaje_texto: str = Form(None),
    image_file: UploadFile = File(None),
    audio_file: UploadFile = File(None),
    modelo: str = Form("flash")
):
    if ACCESS_CODE and clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")

    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        return {"texto": "Error: GEMINI_API_KEY no configurada en el servidor de Render.", "has_audio": False}

    client = genai.Client(api_key=api_key)
    model_name = "gemini-2.5-flash" if modelo == "flash" else "gemini-2.5-pro"

    contents = []
    if image_file and image_file.filename:
        image_bytes = await image_file.read()
        mime_type = image_file.content_type or "image/jpeg"
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    if audio_file and audio_file.filename:
        audio_bytes = await audio_file.read()
        mime_type = audio_file.content_type or "audio/wav"
        contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=mime_type))

    # Inyectar datos en tiempo real de NinjaTrader si existen
    nt_context = ""
    if latest_nt_data:
        nt_context = (
            f"\n\n[MÉTRICAS ACTIVAS EN TIEMPO REAL DESDE NINJATRADER 8]:\n"
            f"- Instrumento: {latest_nt_data.get('symbol')}\n"
            f"- Precio de Ejecución: {latest_nt_data.get('price')}\n"
            f"- VWAP: {latest_nt_data.get('vwap')}\n"
            f"- POC (Point of Control): {latest_nt_data.get('poc')}\n"
            f"- Delta Acumulado (PnL): {latest_nt_data.get('delta')}\n"
            f"- Volumen de Barra: {latest_nt_data.get('volume')}\n"
            f"- Nota de Contexto: {latest_nt_data.get('context_note')}\n"
        )

    system_instruction = (
        "Eres J.A.R.V.I.S., copiloto militar y financiero de Stark Industries, altamente experto en trading de futuros ($MNQ, $MES, $ES). "
        "Analizarás gráficos de NinjaTrader 8 y DeepCharts enfocándote en Order Flow, Volume Profile, "
        "Market Profile, deltas y niveles clave de liquidez.\n\n"
        "REGLAS ESTRICTAS DE CONDUCTA:\n"
        "1. Dirígete al usuario siempre como 'Señor'.\n"
        "2. Mantén un tono sobrio, elegante, calmado, analítico y directo.\n"
        "3. Tus respuestas deben ser sumamente cortas y concisas (máximo 2 o 3 oraciones) para agilizar decisiones de alta frecuencia.\n"
        "4. No uses markdown complejo ni emojis. Habla en español conversacional natural.\n"
        "5. LECTURA DE PRECIOS: Al mencionar precios o niveles, omite completamente los decimales y nunca digas la palabra 'coma'.\n"
        f"6. Utiliza el contexto activo de NinjaTrader 8 para validar tus hipótesis visuales.{nt_context}"
    )

    prompt = mensaje_texto or "Analice la captura de pantalla o audio adjunto bajo su protocolo de trading, Señor."
    contents.append(prompt)

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_instruction)
        )
        respuesta_texto = response.text or "Sin respuesta del modelo."
        texto_filtrado = limpiar_texto_para_voz(respuesta_texto)

        # Generar audio MP3 de la respuesta para reproducir en el cliente
        temp_dir = tempfile.gettempdir()
        audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
        
        communicate = edge_tts.Communicate(texto_filtrado, "es-ES-AlvaroNeural")
        await communicate.save(audio_path)

        return {"texto": respuesta_texto, "has_audio": True}

    except Exception as e:
        return {"texto": f"Error al procesar consulta: {str(e)}", "has_audio": False}

@app.get("/api/audio")
async def get_audio_response():
    temp_dir = tempfile.gettempdir()
    audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
    if os.path.exists(audio_path):
        return FileResponse(audio_path, media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Audio no disponible")

# Proxy WebSocket seguro para la Gemini Multimodal Live API (Modo llamada barra espaciadora)
@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    clave = websocket.query_params.get("clave", "")
    if ACCESS_CODE and clave != ACCESS_CODE:
        await websocket.close(code=1008, reason="Clave de acceso incorrecta")
        return

    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        try:
            await websocket.send_text(json.dumps({"error": "La API Key de Gemini no está configurada en el servidor de Render."}))
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    url = f"{GEMINI_LIVE_URL}?key={api_key}"

    try:
        async with websockets.connect(url) as gemini_ws:
            # 2. Inyectar datos en tiempo real de NinjaTrader 8 en las instrucciones del sistema si existen
            nt_context = ""
            if latest_nt_data:
                nt_context = (
                    f"\\n\\n[DATOS ACTIVOS EN TIEMPO REAL DESDE NINJATRADER 8]:\\n"
                    f"- Instrumento: {latest_nt_data.get('symbol')}\\n"
                    f"- Precio de Ejecución: {latest_nt_data.get('price')}\\n"
                    f"- VWAP: {latest_nt_data.get('vwap')}\\n"
                    f"- POC (Point of Control): {latest_nt_data.get('poc')}\\n"
                    f"- Delta Acumulado: {latest_nt_data.get('delta')}\\n"
                    f"- Volumen de Barra: {latest_nt_data.get('volume')}\\n"
                    f"- Nota de Contexto del Operador: {latest_nt_data.get('context_note')}"
                )

            # Mensaje de configuración inicial del protocolo Live API
            setup_msg = {
                "setup": {
                    "model": "models/gemini-2.0-flash-exp",
                    "generation_config": {
                        "response_modalities": ["AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {"voice_name": "Puck"} # Opciones: Puck, Charon, Kore, Fenrir, Aoede
                            }
                        }
                    },
                    "system_instruction": {
                        "parts": [{
                            "text": (
                                "Eres J.A.R.V.I.S., copiloto militar y financiero de Stark Industries, altamente experto en trading de futuros ($MNQ, $MES, $ES). "
                                "Analizarás gráficos de NinjaTrader 8 y DeepCharts enfocándote en Order Flow, Volume Profile, "
                                "Market Profile, deltas y niveles clave de liquidez.\\n\\n"
                                "REGLAS ESTRICTAS DE CONDUCTA:\\n"
                                "1. Dirígete al usuario siempre como 'Señor'.\\n"
                                "2. Mantén un tono sobrio, elegante, calmado, analítico y directo.\\n"
                                "3. Tus respuestas deben ser cortas y ultra concisas (máximo 2 o 3 oraciones por voz) para agilizar decisiones de alta frecuencia.\\n"
                                "4. No leas reportes largos ni uses markdown o formato de texto, habla en español conversacional natural.\\n"
                                "5. LECTURA DE PRECIOS: Al mencionar precios o niveles, omite completamente los decimales y nunca digas la palabra 'coma'.\\n"
                                f"6. Utiliza el contexto activo de NinjaTrader 8 para validar tus hipótesis visuales.{nt_context}"
                            )
                        }]
                    }
                }
            }
            await gemini_ws.send(json.dumps(setup_msg))

            # 3. Transmisión bidireccional en paralelo
            async def client_to_gemini():
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await gemini_ws.send(msg)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def gemini_to_client():
                try:
                    async for message in gemini_ws:
                        await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(client_to_gemini(), gemini_to_client())

    except Exception as e:
        print(f"Error en WebSocket Live: {e}")
        try:
            await websocket.send_text(json.dumps({
                "error": f"Error de conexión con Google Live API: {str(e)}. Verifique su GEMINI_API_KEY en el panel de Render."
            }))
            await websocket.close()
        except Exception:
            pass
