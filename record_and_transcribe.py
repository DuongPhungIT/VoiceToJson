import whisper
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav

# Cấu hình
SAMPLE_RATE = 16000  # Tốc độ lấy mẫu (16kHz, chuẩn của Whisper)
DURATION = 5  # Thời gian thu âm (giây)
OUTPUT_FILE = "recorded_audio.wav"

def record_audio():
    """Thu âm từ micro và lưu thành file WAV"""
    print(f"🎤 Đang thu âm trong {DURATION} giây...")
    audio_data = sd.rec(int(SAMPLE_RATE * DURATION), samplerate=SAMPLE_RATE, channels=1, dtype=np.int16)
    sd.wait()  # Đợi ghi âm xong
    wav.write(OUTPUT_FILE, SAMPLE_RATE, audio_data)
    print("✅ Thu âm hoàn tất! Đang xử lý...")

def transcribe_audio():
    """Chuyển đổi file âm thanh thành văn bản"""
    model = whisper.load_model("medium")  # Bạn có thể đổi thành 'tiny', 'small', 'large'
    result = model.transcribe(OUTPUT_FILE)
    print("\n📝 Void result:", result)
    print("\n📝 Văn bản nhận diện:")
    print(result["text"])

if __name__ == "__main__":
    record_audio()
    transcribe_audio()