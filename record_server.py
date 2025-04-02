import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "gen-py"))

import whisper
import speech_service.SpeechToText
from thrift.server import TServer
from thrift.transport import TSocket, TTransport
from thrift.protocol import TBinaryProtocol
import tempfile

class SpeechToTextHandler:
    def __init__(self):
        self.model = whisper.load_model("medium")  # Chọn mô hình phù hợp

    def transcribe(self, audio_data):
        with tempfile.NamedTemporaryFile(delete=True, suffix=".wav") as temp_audio:
            temp_audio.write(audio_data)
            temp_audio.flush()
            result = self.model.transcribe(temp_audio.name, language="vi")
            return result["text"]

# Thiết lập server
handler = SpeechToTextHandler()
processor = speech_service.SpeechToText.Processor(handler)
transport = TSocket.TServerSocket(host='127.0.0.1', port=9090)
tfactory = TTransport.TBufferedTransportFactory()
pfactory = TBinaryProtocol.TBinaryProtocolFactory()

server = TServer.TSimpleServer(processor, transport, tfactory, pfactory)
print("🚀 Speech-to-Text Thrift server is running on port 9090...")
server.serve()
