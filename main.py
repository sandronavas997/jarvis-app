import os
import re
import tempfile
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from google import genai
from google.genai import types
import edge_tts

app = FastAPI()

# Configuración de claves
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
gemini_api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

def limpiar_texto_para_voz(texto: str) -> str:
    texto = re.sub(r'[\*\#\_\`\~\>]', '', texto)
    texto = re.sub(r'[\U00010000-\U0010ffff]', '', texto)
    return texto.strip()

@app.get("/")
async def read_root():
    if os.path.exists("templates/index.html"):
        return FileResponse("templates/index.html")
    return HTMLResponse("<h1>Error: No se encontró el archivo index.html en templates/</h1>")

@app.post("/api/chat")
async def chat(
    clave: str = Form(""),
    mensaje_texto: str = Form(None),
    image_file: UploadFile = File(None),
    modelo: str = Form("flash")
):
    # Verificación de clave de acceso
    if ACCESS_CODE and clave != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Clave de acceso incorrecta.")

    if not client:
        return {"texto": "Error: La variable GEMINI_API_KEY no está configurada en Render."}

    # Nombres de modelos actualizados
    model_name = "gemini-3.6-flash" if modelo == "flash" else "gemini-3.6-pro"

    contents = []
    if image_file and image_file.filename:
        image_bytes = await image_file.read()
        mime_type = image_file.content_type or "image/jpeg"
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    prompt = mensaje_texto or "Analiza la imagen adjunta."
    contents.append(prompt)

    system_instruction = (
        "Eres JARVIS, un asistente inteligente, elegante, eficiente y respetuoso. "
        "Responde de forma clara y directa en español. Dirígete al usuario como Señor."
    )

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_instruction)
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
