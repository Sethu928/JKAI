import os
import pyaudio
import wave
import pyttsx3
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

engine = pyttsx3.init()
engine.setProperty("rate", 175)
voices = engine.getProperty("voices")
for v in voices:
    if "french" in v.name.lower() or "french" in v.id.lower():
        engine.setProperty("voice", v.id)
        break

def speak(text):
    engine.say(text)
    engine.runAndWait()

def record_audio(duration=5, filename="temp_audio.wav"):
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    print("J-KAI > En écoute...", flush=True)
    frames = []
    for _ in range(0, int(RATE / CHUNK * duration)):
        data = stream.read(CHUNK)
        frames.append(data)
    stream.stop_stream()
    stream.close()
    p.terminate()
    wf = wave.open(filename, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    return filename

def listen():
    filename = record_audio(duration=5)
    with open(filename, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="fr"
        )
    os.remove(filename)
    text = result.text.strip()
    print(f"SethU (vocal) > {text}", flush=True)
    return text