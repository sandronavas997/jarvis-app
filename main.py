import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import websockets

app = FastAPI(title="JARVIS Copilot - WebSockets Edition")

# URL de la API Multimodal Live de Gemini (v1alpha)
GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"

# Configuración de seguridad y credenciales
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

@app.get("/")
async def read_root():
    if os.path.exists("templates/index.html"):
        return FileResponse("templates/index.html")
    return HTMLResponse("<h1>Error: No se encontró el archivo index.html en la carpeta templates/</h1>")

# Endpoint para NinjaTrader 8 (Mantiene el canal de datos C# activo)
@app.post("/api/ninjatrader")
async def ninjatrader_feed(payload: NinjaTraderPayload):
    global latest_nt_data
    if ACCESS_CODE and payload.clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")
    
    # Actualizar memoria en vivo en el servidor
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

# Proxy WebSocket seguro para la Gemini Multimodal Live API
@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # 1. Validar la clave de acceso para seguridad del WebSocket
    clave = websocket.query_params.get("clave", "")
    if ACCESS_CODE and clave != ACCESS_CODE:
        await websocket.close(code=1008, reason="Clave de acceso incorrecta")
        return

    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        await websocket.close(code=1008, reason="API Key no configurada en el servidor")
        return

    # Construir la URL de conexión segura a Google
    url = f"{GEMINI_LIVE_URL}?key={api_key}"

    try:
        async with websockets.connect(url) as gemini_ws:
            # 2. Inyectar datos en tiempo real de NinjaTrader 8 en las instrucciones del sistema si existen
            nt_context = ""
            if latest_nt_data:
                nt_context = (
                    f"\n\n[DATOS ACTIVOS EN TIEMPO REAL DESDE NINJATRADER 8]:\n"
                    f"- Instrumento: {latest_nt_data.get('symbol')}\n"
                    f"- Precio de Ejecución: {latest_nt_data.get('price')}\n"
                    f"- VWAP: {latest_nt_data.get('vwap')}\n"
                    f"- POC (Point of Control): {latest_nt_data.get('poc')}\n"
                    f"- Delta Acumulado: {latest_nt_data.get('delta')}\n"
                    f"- Volumen de Barra: {latest_nt_data.get('volume')}\n"
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
                                "Market Profile, deltas y niveles clave de liquidez.\n\n"
                                "REGLAS STRICTAS DE CONDUCTA:\n"
                                "1. Dirígete al usuario siempre como 'Señor'.\n"
                                "2. Mantén un tono sobrio, elegante, calmado, analítico y directo.\n"
                                "3. Tus respuestas deben ser cortas y ultra concisas (máximo 2 o 3 oraciones por voz) para agilizar decisiones de alta frecuencia.\n"
                                "4. No leas reportes largos ni uses markdown o formato de texto, habla en español conversacional natural.\n"
                                "5. LECTURA DE PRECIOS: Al mencionar precios o niveles, omite completamente los decimales y nunca digas la palabra 'coma'.\n"
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

            async def gemini_to_client():
                try:
                    async for message in gemini_ws:
                        await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(client_to_gemini(), gemini_to_client())

    except Exception as e:
        print(f"Error en WebSocket Live: {e}")
        await websocket.close()
