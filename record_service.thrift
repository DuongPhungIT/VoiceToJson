namespace py record_service

service SpeechToText {
    string transcribe(1: binary audio_data)
}