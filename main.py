import os
import re
import tempfile
import asyncio
import json
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Set
import websockets
from google import genai
from google.genai import types
import edge_tts

app = FastAPI(title="J.A.R.V.I.S. Copilot - Ultimate Edition")

# URL de la API Multimodal Live de Gemini (v1alpha)
GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"

# Configuración de seguridad y credenciales
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Memoria global en RAM para almacenar las últimas métricas de NinjaTrader 8
latest_nt_data = {}

# Almacén de conexiones activas para retransmisión de datos
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
    swing_high_20: Optional[float] = None
    swing_low_20: Optional[float] = None
    candle_body: Optional[float] = None
    upper_wick: Optional[float] = None
    lower_wick: Optional[float] = None
    context_note: Optional[str] = ""
    modelo: Optional[str] = "flash"

SYSTEM_INSTRUCTION = """Eres J.A.R.V.I.S., copiloto militar y financiero de Stark Industries, altamente experto en trading de futuros ($MNQ, $MES, $ES).
Analizarás gráficos de NinjaTrader 8 y DeepCharts enfocándote en Order Flow, Volume Profile, Market Profile, deltas y niveles clave de liquidez.

REGLAS ESTRICTAS DE CONDUCTA:
1. Dirígete al usuario siempre como 'Señor'.
2. Mantén un tono sobrio, elegante, calmado, analítico y directo (estilo militar de Iron Man).
3. Tus respuestas por voz deben ser ultra cortas y concisas (máximo 2 o 3 oraciones por intervención) para agilizar decisiones de alta frecuencia.
4. No leas reportes largos ni uses markdown o formato de texto, habla en español conversacional natural.
5. LECTURA DE PRECIOS: Al mencionar precios o niveles, omite completamente los decimales y nunca digas la palabra 'coma' o 'punto' al leerlos (por ejemplo, di 21450 en lugar de 21450,25).
6. Utiliza el contexto activo de NinjaTrader 8 para validar tus hipótesis visuales.
"""

def limpiar_texto_para_voz(texto: str) -> str:
    # Eliminar símbolos de markdown
    texto = re.sub(r"[\*\#\_\`\~\>]", "", texto)
    # Eliminar emojis y caracteres no pronunciables
    texto = re.sub(r"[\U00010000-\U0010ffff]", "", texto)
    return texto.strip()

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
        "swing_high_20": payload.swing_high_20,
        "swing_low_20": payload.swing_low_20,
        "candle_body": payload.candle_body,
        "upper_wick": payload.upper_wick,
        "lower_wick": payload.lower_wick,
        "context_note": payload.context_note
    }
    print(f"[NINJATRADER 8 FEED] Datos actualizados: {latest_nt_data}")

    # OPTIMIZACIÓN EN FLUJO ACTIVO: Si hay un WebSocket de voz abierto, inyectamos la actualización de NinjaTrader en RAM
    if active_connections:
        update_text = (
            f"[SISTEMA - ACTUALIZACIÓN EN TIEMPO REAL DESDE NINJATRADER 8]:\n"
            f"- Instrumento: {latest_nt_data['symbol']}\n"
            f"- Precio: {latest_nt_data['price']}\n"
            f"- VWAP: {latest_nt_data['vwap'] if latest_nt_data['vwap'] is not None else 'N/A'}\n"
            f"- POC: {latest_nt_data['poc'] if latest_nt_data['poc'] is not None else 'N/A'}\n"
            f"- Delta Acumulado (PnL Flotante): {latest_nt_data['delta'] if latest_nt_data['delta'] is not None else 'N/A'}\n"
            f"- Volumen: {latest_nt_data['volume'] if latest_nt_data['volume'] is not None else 'N/A'}\n"
            f"- Swing High (20): {latest_nt_data['swing_high_20'] if latest_nt_data['swing_high_20'] is not None else 'N/A'}\n"
            f"- Swing Low (20): {latest_nt_data['swing_low_20'] if latest_nt_data['swing_low_20'] is not None else 'N/A'}\n"
            f"- Cuerpo de Vela: {latest_nt_data['candle_body'] if latest_nt_data['candle_body'] is not None else 'N/A'}\n"
            f"- Mecha Superior: {latest_nt_data['upper_wick'] if latest_nt_data['upper_wick'] is not None else 'N/A'}\n"
            f"- Mecha Inferior: {latest_nt_data['lower_wick'] if latest_nt_data['lower_wick'] is not None else 'N/A'}\n"
            f"- Nota del Operador: {latest_nt_data['context_note']}\n"
            f"Por favor, ten en cuenta estos datos estructurales actualizados para mis próximas preguntas."
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
                print("[REAL-TIME BROADCAST] Datos de NinjaTrader inyectados en la sesión de Gemini.")
            except Exception as e:
                print(f"Error retransmitiendo datos a Gemini WebSocket: {e}")

    return {"status": "success", "message": "Métricas de mercado actualizadas en JARVIS"}

# Endpoint para la interfaz Web en modo HTTP Chat (Texto + Archivos)
@app.post("/api/chat")
async def chat(
    clave: str = Form(""),
    mensaje_texto: str = Form(None),
    image_file: UploadFile = File(None),
    modelo: str = Form("flash")
):
    if ACCESS_CODE and clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")

    # Inicializar cliente de GenAI al vuelo si no está listo
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        return {"texto": "Error: La variable GEMINI_API_KEY no está configurada."}

    genai_client = genai.Client(api_key=api_key)
    model_name = "gemini-3.6-pro" if modelo == "pro" else "gemini-3.6-flash"

    # Contexto dinámico de NinjaTrader
    nt_context = ""
    if latest_nt_data:
        nt_context = (
            f"\n\n[DATOS NINJATRADER 8 EN TIEMPO REAL]:\n"
            f"- Instrumento: {latest_nt_data.get('symbol')}\n"
            f"- Precio: {latest_nt_data.get('price')}\n"
            f"- VWAP: {latest_nt_data.get('vwap')}\n"
            f"- POC: {latest_nt_data.get('poc')}\n"
            f"- Delta Acumulado (PnL Flotante): {latest_nt_data.get('delta')}\n"
            f"- Volumen: {latest_nt_data.get('volume')}\n"
            f"- Swing High (20): {latest_nt_data.get('swing_high_20')}\n"
            f"- Swing Low (20): {latest_nt_data.get('swing_low_20')}\n"
            f"- Cuerpo de Vela: {latest_nt_data.get('candle_body')}\n"
            f"- Mecha Superior: {latest_nt_data.get('upper_wick')}\n"
            f"- Mecha Inferior: {latest_nt_data.get('lower_wick')}\n"
            f"- Nota del Operador: {latest_nt_data.get('context_note')}"
        )

    profundidad_instruccion = (
        "El Señor ha solicitado un análisis PROFUNDO (modelo PRO). Proporciona un desglose detallado de la estructura de mercado, desequilibrios, zonas de liquidez y planes de trading estructurados basados en acción del precio."
        if modelo == "pro" else
        "El Señor busca respuestas ULTRA RÁPIDAS (modelo FLASH). Sé extremadamente breve, limita tu respuesta a un máximo de 2 o 3 oraciones concisas de ejecución táctica."
    )

    full_system_instruction = (
        f"{SYSTEM_INSTRUCTION}\n"
        f"MODO ACTIVO: {profundidad_instruccion}\n"
        f"{nt_context}"
    )

    contents = []
    if image_file and image_file.filename:
        image_bytes = await image_file.read()
        mime_type = image_file.content_type or "image/jpeg"
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    prompt = mensaje_texto or "Analice la captura adjunta y evalúe la estructura del mercado."
    contents.append(prompt)

    try:
        response = genai_client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=full_system_instruction)
        )
        
        texto_respuesta = response.text or "Sin respuesta."
        texto_voz = limpiar_texto_para_voz(texto_respuesta)

        has_audio = False
        if texto_voz:
            temp_dir = tempfile.gettempdir()
            audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
            communicate = edge_tts.Communicate(texto_voz, "es-MX-JorgeNeural")
            await communicate.save(audio_path)
            has_audio = True

        return {"texto": texto_respuesta, "has_audio": has_audio}

    except Exception as e:
        return {"texto": f"Error al procesar la consulta: {str(e)}"}

@app.get("/api/audio")
async def get_audio():
    temp_dir = tempfile.gettempdir()
    audio_path = os.path.join(temp_dir, "jarvis_response.mp3")
    if os.path.exists(audio_path):
        return FileResponse(audio_path, media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Audio no disponible")

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

    # Determinar el tipo de profundidad según el modelo seleccionado
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
                    f"- Swing High (20): {latest_nt_data.get('swing_high_20')}\n"
                    f"- Swing Low (20): {latest_nt_data.get('swing_low_20')}\n"
                    f"- Cuerpo de Vela: {latest_nt_data.get('candle_body')}\n"
                    f"- Mecha Superior: {latest_nt_data.get('upper_wick')}\n"
                    f"- Mecha Inferior: {latest_nt_data.get('lower_wick')}\n"
                    f"- Nota de Contexto del Operador: {latest_nt_data.get('context_note')}"
                )

            # Adaptamos las instrucciones del sistema dinámicamente según si eligieron "Pro" (análisis profundo) o "Flash" (máxima velocidad)
            profundidad_instruccion = (
                "Señor ha solicitado un INFORME PROFUNDO. Proporciona análisis técnicos de Order Flow muy detallados, estructura de mercado, desequilibrios y planes estructurados de acción del precio."
                if modelo == "pro" else
                "Señor busca RESPUESTAS ULTRA RÁPIDAS. Sé sumamente conciso, limita tus respuestas a un máximo de 1 o 2 oraciones por intervención."
            )

            system_instruction_text = (
                f"{SYSTEM_INSTRUCTION}\n"
                f"MODO ACTIVO: {profundidad_instruccion}\n"
                f"{nt_context}"
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
