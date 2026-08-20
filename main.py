import os
import json
import asyncio
import re
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, Set
import websockets

app = FastAPI(title="JARVIS Copilot - WebSockets Edition v5")

# URL de la API Multimodal Live de Gemini (v1alpha)
GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"

# Configuración de seguridad y credenciales
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Memoria global para almacenar las últimas métricas de NinjaTrader 8
latest_nt_data = {}

# Almacén de WebSockets de clientes activos para poder retransmitir actualizaciones en tiempo real
class ActiveConnection:
    def __init__(self, websocket: WebSocket, gemini_ws: websockets.WebSocketClientProtocol):
        self.websocket = websocket
        self.gemini_ws = gemini_ws

active_connections: Set[ActiveConnection] = set()

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
    print(f"[NINJATRADER 8 FEED] Datos recibidos: {latest_nt_data}")

    # Si hay una conversación WebSocket activa, inyectamos los nuevos datos como un "clientContent" turn
    # Esto actualiza la memoria de J.A.R.V.I.S en pleno vuelo sin tener que reiniciar la llamada.
    if active_connections:
        update_text = (
            f"[SISTEMA - ACTUALIZACIÓN EN TIEMPO REAL DESDE NINJATRADER 8]:\n"
            f"- Instrumento: {latest_nt_data['symbol']}\n"
            f"- Precio: {latest_nt_data['price']}\n"
            f"- VWAP: {latest_nt_data['vwap']}\n"
            f"- POC: {latest_nt_data['poc']}\n"
            f"- Delta Acumulado: {latest_nt_data['delta']}\n"
            f"- Volumen: {latest_nt_data['volume']}\n"
            f"- Nota: {latest_nt_data['context_note']}\n"
            f"Por favor, ten en cuenta estos datos exactos para mis próximas preguntas."
        )
        
        client_turn = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": update_text}
                        ]
                    }
                ],
                "turnComplete": False
            }
        }
        
        for conn in list(active_connections):
            try:
                await conn.gemini_ws.send(json.dumps(client_turn))
                print(f"[REAL-TIME BROADCAST] Datos de NinjaTrader inyectados en la sesión de Gemini.")
            except Exception as e:
                print(f"Error retransmitiendo datos a Gemini WebSocket: {e}")

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

    # NOTA TÉCNICA CRÍTICA: La Live API (Bidi WebSocket) de Google actualmente solo acepta el modelo Multimodal Live
    # que es gemini-2.0-flash-exp (o su alias realtime). Si intentamos usar "gemini-3.6-pro" o "gemini-3.6-flash" en Bidi,
    # Google cerrará la conexión con un error 400. Controlamos esto enrutando al motor realtime correspondiente.
    modelo = websocket.query_params.get("modelo", "flash")
    target_model = "models/gemini-2.0-flash-exp" # El motor nativo realtime de Google

    try:
        async with websockets.connect(url) as gemini_ws:
            # Crear y registrar la conexión activa para actualizaciones en vivo
            conn_obj = ActiveConnection(websocket, gemini_ws)
            active_connections.add(conn_obj)

            # Preparar contexto inicial de NinjaTrader
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

            # Adaptamos las instrucciones del sistema dinámicamente según si eligieron "Pro" (análisis profundo) o "Flash" (máxima velocidad)
            profundidad_instruccion = (
                "Señor ha solicitado un INFORME PROFUNDO. Proporciona análisis técnicos de Order Flow muy detallados, estructura de mercado, desequilibrios y planes estructurados."
                if modelo == "pro" else
                "Señor busca RESPUESTAS ULTRA RÁPIDAS. Sé sumamente conciso, limita tus respuestas a un máximo de 1 o 2 oraciones por intervención."
            )

            system_instruction_text = (
                "Eres J.A.R.V.I.S., copiloto militar y financiero de Stark Industries, altamente experto en trading de futuros ($MNQ, $MES, $ES). "
                "Analizarás gráficos de NinjaTrader 8 y DeepCharts enfocándote en Order Flow, Volume Profile, "
                "Market Profile, deltas y niveles clave de liquidez.\n\n"
                f"MODO ACTIVO: {profundidad_instruccion}\n\n"
                "REGLAS ESTRICTAS DE CONDUCTA:\n"
                "1. Dirígete al usuario siempre como 'Señor'.\n"
                "2. Mantén un tono sobrio, elegante, calmado, analítico y directo.\n"
                "3. No leas reportes largos ni uses markdown complejo, habla y escribe en español conversacional natural.\n"
                "4. LECTURA DE PRECIOS: Al mencionar precios o niveles, omite completamente los decimales y nunca digas la palabra 'coma'.\n"
                f"5. Utiliza el contexto activo de NinjaTrader 8 para validar tus hipótesis visuales.{nt_context}"
            )

            # Mensaje de configuración inicial del protocolo Live API
            setup_msg = {
                "setup": {
                    "model": target_model,
                    "generation_config": {
                        "response_modalities": ["TEXT", "AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {"voice_name": "Puck"} # Opciones de voz: Puck, Charon, Kore, Fenrir, Aoede
                            }
                        }
                    },
                    "system_instruction": {
                        "parts": [{"text": system_instruction_text}]
                    }
                }
            }
            await gemini_ws.send(json.dumps(setup_msg))

            # Transmisión bidireccional en paralelo
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
    finally:
        # Remover conexión activa al desconectar
        for conn in list(active_connections):
            if conn.websocket == websocket:
                active_connections.remove(conn)
                break
        try:
            await websocket.close()
        except Exception:
            pass
