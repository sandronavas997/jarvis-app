import os
import json
import io
import re
import asyncio
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types
import edge_tts

app = FastAPI(title="JARVIS Copilot")
templates = Jinja2Templates(directory="templates")

api_key = os.getenv("GEMINI_API_KEY", "TU_API_KEY_AQUI")
client = genai.Client(api_key=api_key)
FILE_MEMORIA = "memoria_sesion.json"

JARVIS_SYSTEM_PROMPT = """
Eres JARVIS, copiloto de trading e inteligencia artificial experto en Acción del Precio, lectura de mercado y futuros $MNQ.
Reglas de conducta:
1. Dirígete al usuario siempre como 'Señor'.
2. Tono sobrio, táctico y profesional.
3. Responde en español hablado fluido y natural. 
4. PROHIBIDO incluir marcas de tiempo, códigos de subtítulos o metadatos.
5. PROHIBIDO EL USO DE EMOJIS O EMOTICONES en el texto.
6. CONTROL DE LONGITUD:
   - Por defecto: Sé ultra conciso (máximo 2 oraciones breves) para rapidez por voz.
   - Si el Señor dice 'INFORME' o 'PROFUNDIZA': Extiéndete con un desglose técnico detallado.
7. LECTURA DE PRECIOS: Al mencionar precios o niveles, omite completamente los decimales y nunca digas la palabra 'coma'.
"""

def limpiar_texto_para_voz(texto):
    return re.sub(r'[^\w\s,.:;?!áéíóúÁÉÍÓÚñÑ$%-]', '', texto)

def cargar_memoria():
    if os.path.exists(FILE_MEMORIA):
        try:
            with open(FILE_MEMORIA, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def guardar_memoria(historial):
    with open(FILE_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=2)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/history")
async def get_history():
    return cargar_memoria()

@app.post("/api/chat")
async def chat_endpoint(
    mensaje_texto: str = Form(None),
    audio_file: UploadFile = File(None),
    image_file: UploadFile = File(None),
    modelo: str = Form("flash")
):
    historial = cargar_memoria()
    ruta_datos = "datos_sesion.json"
    datos_json = "{}"
    if os.path.exists(ruta_datos):
        with open(ruta_datos, "r") as f:
            datos_json = f.read()

    contexto = ""
    if historial:
        contexto = "\n[HISTORIAL DE LA SESIÓN]:\n" + "\n".join(historial[-10:]) + "\n"

    prompt_text = (
        f"{JARVIS_SYSTEM_PROMPT}\n{contexto}\n"
        f"[DATOS NINJATRADER]:\n{datos_json}\n\n"
        "Responde a la solicitud del Señor."
    )
    if mensaje_texto:
        prompt_text += f"\n[TEXTO DEL SEÑOR]: {mensaje_texto}"

    partes = [prompt_text]

    if image_file:
        img_bytes = await image_file.read()
        partes.append(types.Part.from_bytes(data=img_bytes, mime_type=image_file.content_type or "image/jpeg"))

    if audio_file:
        audio_bytes = await audio_file.read()
        partes.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_file.content_type or "audio/wav"))

    # Modelos actualizados a versión 3.6
    target_model = 'gemini-3.6-pro' if modelo == 'pro' else 'gemini-3.6-flash'

    config_directo = types.GenerateContentConfig(temperature=0.3)
    response = client.models.generate_content(
        model=target_model,
        contents=partes,
        config=config_directo
    )
    respuesta_texto = response.text

    pregunta_label = mensaje_texto if mensaje_texto else "[Consulta de audio/imagen]"
    historial.append(f"Señor ({modelo.upper()}): {pregunta_label}")
    historial.append(f"JARVIS: {respuesta_texto}")
    guardar_memoria(historial)

    texto_filtrado = limpiar_texto_para_voz(respuesta_texto)
    archivo_audio_res = os.path.abspath("jarvis_response.mp3")
    communicate = edge_tts.Communicate(texto_filtrado, "es-ES-AlvaroNeural")
    await communicate.save(archivo_audio_res)

    return {
        "texto": respuesta_texto,
        "audio_url": "/api/audio-response"
    }

@app.get("/api/audio-response")
async def get_audio_response():
    archivo_audio_res = os.path.abspath("jarvis_response.mp3")
    if os.path.exists(archivo_audio_res):
        return FileResponse(archivo_audio_res, media_type="audio/mpeg")
    return {"error": "Audio no disponible"}