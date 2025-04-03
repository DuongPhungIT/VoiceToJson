from flask import Flask, request, jsonify, render_template, session
import speech_recognition as sr
import google.generativeai as genai
import logging
import json
import os
from pydub import AudioSegment
import re
from datetime import datetime
from src.product_menu import get_menu
import time
import socket
import traceback

# Khởi tạo logger global
logger = None

def setup_logging():
    global logger
    current_date = datetime.now().strftime('%Y%m%d')
    os.makedirs('logs', exist_ok=True)
    os.makedirs('logsError', exist_ok=True)

    # Xóa tất cả handlers cũ
    logger = logging.getLogger()
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Format cho server log (tổng quan)
    server_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Format cho error log (chi tiết)
    error_formatter = logging.Formatter(
        '%(asctime)s\n%(message)s\n',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Handler cho server log
    server_handler = logging.FileHandler(f'logs/server_{current_date}.log', encoding='utf-8')
    server_handler.setFormatter(server_formatter)
    server_handler.setLevel(logging.INFO)

    # Handler cho error log
    error_handler = logging.FileHandler(f'logsError/error_{current_date}.log', encoding='utf-8')
    error_handler.setFormatter(error_formatter)
    error_handler.setLevel(logging.ERROR)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(server_formatter)

    # Setup root logger (chỉ cho log tổng quan)
    logger.setLevel(logging.INFO)
    logger.addHandler(server_handler)
    logger.addHandler(console_handler)

    # Setup error logger riêng biệt
    error_logger = logging.getLogger('error_logger')
    error_logger.setLevel(logging.ERROR)
    error_logger.addHandler(error_handler)
    error_logger.propagate = False  # Không cho phép log lan truyền lên root logger

    # Tắt log của các module khác
    logging.getLogger('werkzeug').disabled = True
    logging.getLogger('flask').disabled = True
    logging.getLogger('urllib3').disabled = True
    logging.getLogger('google').disabled = True
    logging.getLogger('google.generativeai').disabled = True
    logging.getLogger('speech_recognition').disabled = True

    return error_logger  # Trả về error logger để sử dụng

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Cần thiết cho session

# Cấu hình Gemini
GOOGLE_API_KEY = "AIzaSyC7aoIVhPcI8lJHEIfku-QKJPG-1BAOZBc"
genai.configure(api_key=GOOGLE_API_KEY)

try:
    model = genai.GenerativeModel('gemini-2.0-flash')
except Exception as e:
    logger.error(f"Failed to initialize Gemini model: {str(e)}")
    model = None

# Khởi tạo current_order là một dict toàn cục
current_order = {
    "products": [],
    "productsError": [],
    "order_status": "new",
    "total_items": 0,
    "created_at": None,
    "updated_at": None,
    "message": ""
}

# Tạo hằng số cho đường dẫn lưu file
AUDIO_UPLOAD_FOLDER = 'audio_records'

# Đảm bảo thư mục tồn tại
if not os.path.exists(AUDIO_UPLOAD_FOLDER):
    os.makedirs(AUDIO_UPLOAD_FOLDER)

def create_prompt(question):
    menu = get_menu()
    return f"""
    Câu hỏi: {question}
    
    Bạn là một hệ thống xử lý đơn hàng bánh bao. Nhiệm vụ của bạn là phân tích đơn hàng và tìm sản phẩm chính xác trong menu.

    MENU SẢN PHẨM:
    {menu}

    QUY TẮC XỬ LÝ ĐƠN HÀNG:

    1. TÌM KIẾM SẢN PHẨM:
       - Chỉ chấp nhận sản phẩm có trong menu
       - Phải khớp 100% tên sản phẩm hoặc từ khóa
       - Không chấp nhận sản phẩm gần giống
       - Không chấp nhận sản phẩm có tên khác
       - Không chấp nhận sản phẩm có trọng lượng khác
       - Không chấp nhận sản phẩm có thương hiệu khác

    2. XÁC ĐỊNH SỐ LƯỢNG:
       a) Chuyển đổi chữ thành số:
          - "một" hoặc "1" -> 1
          - "hai" hoặc "2" -> 2
          - "ba" hoặc "3" -> 3
          - "bốn" hoặc "4" -> 4
          - "năm" hoặc "5" -> 5
          - "sáu" hoặc "6" -> 6
          - "bảy" hoặc "7" -> 7
          - "tám" hoặc "8" -> 8
          - "chín" hoặc "9" -> 9
          - "mười" hoặc "10" -> 10
          - "hai mươi" hoặc "20" -> 20
          - "hai mươi ba" hoặc "23" -> 23
          - "ba mươi" hoặc "30" -> 30
          - "ba mươi lăm" hoặc "35" -> 35
          - "bốn mươi" hoặc "40" -> 40
          - "bốn mươi lăm" hoặc "45" -> 45
          - "năm mươi" hoặc "50" -> 50

       b) Quy tắc ưu tiên số lượng:
          - Số lượng là số gần nhất với từ "cái"
          - Nếu có nhiều số gần từ "cái", lấy số đầy đủ (ví dụ: "23 cái" -> 23)
          - Số ở đầu câu là số thứ tự, không phải số lượng
          - Nếu không có từ "cái" và số, mặc định là 1
          - Nếu không xác định được số lượng, mặc định là 1
          - Số ở cuối câu là số lượng của sản phẩm cuối cùng
          - Số lượng phải là số nguyên dương

    3. XỬ LÝ SẢN PHẨM KHÔNG TÌM THẤY:
       - Đưa vào productsError nếu không tìm thấy trong menu
       - Không thay thế bằng sản phẩm khác
       - Không đoán sản phẩm
       - Không bỏ qua sản phẩm
       - Bỏ các từ chỉ thứ tự như "bà", "ba" khi đưa vào productsError
       - Phải đưa tất cả sản phẩm không tìm thấy vào productsError
       - Không được bỏ sót bất kỳ sản phẩm nào không tìm thấy

    4. CÁC TRƯỜNG HỢP ĐẶC BIỆT:
       a) Thương hiệu:
          - Mặc định là "Thọ Phát" nếu không có thương hiệu
          - Phải khớp chính xác thương hiệu

       b) Từ khóa đặc biệt:
          - "cao su" = "cua xanh"
          - "theo" = "heo"
          - "Cúc" = "cút"
          - "chướng ngại" = "xíu mại"
          - "tôn" = "tôm"
          - "Amazon" = "hamburger"
          - "Phượng" = "cuộn"
          - "Công" = "cuộn"
          - "bánh kẹp" = "bánh kẹp"
          - "bánh trái đào" = "đào"
          - "bánh dorayaki" = "dorayaki"
          - "kosan" = "cua xanh"
          - "cục" = "cuộn"
          - "mini" = "cua xanh"
          
       c) Xử lý các trường hợp đặc biệt:
            - Nếu khách nói "bánh bao" -> tìm sản phẩm bánh bao
            - Nếu khách nói "bánh giò" -> tìm sản phẩm bánh giò
            - Nếu khách nói "cảo" -> tìm sản phẩm há cảo
            - Nếu khách nói "chay" -> tìm sản phẩm nhân chay
            - Nếu khách nói "sữa tươi" -> tìm sản phẩm có sữa tươi
            - Nếu khách nói "thập cẩm" -> tìm sản phẩm thập cẩm
            - Nếu khách nói "xúc xích" -> tìm sản phẩm xúc xích
            - Nếu khách nói "phô mai" -> tìm sản phẩm phô mai
            - Nếu khách nói "hoa hồng" -> tìm sản phẩm hoa hồng
            - Nếu khách nói "tôm" -> tìm sản phẩm tôm
            - Nếu khách nói "xíu mại" -> tìm sản phẩm xíu mại
            - Nếu khách nói "hamburger" -> tìm sản phẩm hamburger
            - Nếu khách nói "đào" -> tìm sản phẩm bánh trái đào


    FORMAT KẾT QUẢ:
    {{
        "products": [
            {{
                "name": "Tên sản phẩm từ menu",
                "quantity": số_lượng,
                "sapCode": mã_sản_phẩm,
                "description": "Lý do chọn sản phẩm này"
            }}
        ],
        "productsError": [
            {{
                "name": "Tên sản phẩm không tìm thấy",
                "quantity": số_lượng
            }}
        ]
    }}

    VÍ DỤ:
    Input: "bầu cua xanh vani 260 g 3 cái 3 cuộn vàng 300 g 4 cái bao hoa cúc 500g 7 cái bao hoa hồng 264 g"

    Output:
    {{
        "products": [
            {{
                "name": "Bánh Bao Thọ Phát KN Cua Xanh Vani 260g(65gx4)",
                "quantity": 3,
                "sapCode": "5000095",
                "description": "Khớp với yêu cầu: bầu cua xanh vani 260 g"
            }},
            {{
                "name": "Bánh bao Thọ Phát Không Nhân Dài Cuộn Vàng 300g-25gx12",
                "quantity": 4,
                "sapCode": "5000099",
                "description": "Khớp với yêu cầu: cuộn vàng 300 g"
            }},
            {{
                "name": "Bánh Bao Thọ Phát Không Nhân Hoa Hồng HDL 264g-22gx12",
                "quantity": 7,
                "sapCode": "5000307",
                "description": "Khớp với yêu cầu: bao hoa hồng 264 g"
            }}
        ],
        "productsError": [
            {{
                "name": "bầu cua xanh vani",
                "quantity": 3
            }},
            {{
                "name": "bao hoa cúc",
                "quantity": 7
            }}
        ]
    }}

    QUY TẮC QUAN TRỌNG:
    1. Chỉ chấp nhận sản phẩm có trong menu
    2. Không thay thế sản phẩm không tìm thấy
    3. Không đoán sản phẩm
    4. Không bỏ qua sản phẩm
    5. Phải khớp 100% tên sản phẩm
    6. Phải khớp 100% trọng lượng
    7. Phải khớp 100% thương hiệu
    8. Phải xử lý tất cả sản phẩm trong đơn hàng
    9. Phải đưa sản phẩm không tìm thấy vào productsError
    10. Phải kiểm tra kỹ từng sản phẩm trước khi đưa vào products
    11. Nếu không xác định được số lượng, mặc định là 1
    12. Phải chuyển đổi số bằng chữ thành số
    13. Số ở cuối câu là số lượng của sản phẩm cuối cùng
    14. Bỏ các từ chỉ thứ tự khi đưa vào productsError
    15. Ưu tiên lấy số lượng từ từ "cái" trước
    16. Nếu có nhiều số gần từ "cái", lấy số đầy đủ
    17. Phải đưa tất cả sản phẩm không tìm thấy vào productsError
    18. Không được bỏ sót bất kỳ sản phẩm nào không tìm thấy
    """

def transcribe_audio(audio_data):
    try:
        start_time = time.time()
        # Chuyển đổi WebM sang WAV
        audio = AudioSegment.from_file(audio_data, format="webm")
        audio = audio.set_channels(1)
        audio = audio.set_frame_rate(16000)
        logger.info(f"Chuyển đổi WebM sang WAV: {time.time() - start_time:.3f}s")
        
        # Lưu file WAV tạm thời
        save_start = time.time()
        temp_wav = "temp_audio.wav"
        audio.export(temp_wav, format="wav")
        logger.info(f"Lưu file WAV tạm thời: {time.time() - save_start:.3f}s")
        
        # Điều chỉnh nhiễu
        noise_start = time.time()
        audio = AudioSegment.from_wav(temp_wav)
        audio = audio - 10  # Giảm âm lượng 10dB
        logger.info(f"Điều chỉnh nhiễu: {time.time() - noise_start:.3f}s")
        
        # Lưu file đã điều chỉnh
        save_adjusted_start = time.time()
        audio.export(temp_wav, format="wav")
        logger.info(f"Lưu file đã điều chỉnh: {time.time() - save_adjusted_start:.3f}s")
        
        # Nhận dạng giọng nói
        recognition_start = time.time()
        recognizer = sr.Recognizer()
        with sr.AudioFile(temp_wav) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="vi-VN")
        logger.info(f"Nhận dạng giọng nói: {time.time() - recognition_start:.3f}s")
        
        # Xóa file tạm
        cleanup_start = time.time()
        os.remove(temp_wav)
        logger.info(f"Xóa file tạm: {time.time() - cleanup_start:.3f}s")
        
        total_time = time.time() - start_time
        logger.info(f"Tổng thời gian xử lý audio: {total_time:.3f}s")
        
        return text
        
    except Exception as e:
        error_logger = logging.getLogger('error_logger')
        error_logger.error(f"Lỗi khi xử lý audio: {str(e)}")
        error_logger.error(traceback.format_exc())
        raise

@app.route('/')
def index():
    return render_template('index.html')

def process_with_gemini(text):
    global current_order
    try:
        prompt = create_prompt(text)
        response = model.generate_content(prompt)
        
        if not response or not hasattr(response, 'text'):
            raise Exception("Không nhận được phản hồi từ Gemini")
            
        answer_text = response.text.strip()
        answer_text = re.sub(r'```json\s*|\s*```', '', answer_text)
        
        gemini_response = json.loads(answer_text)
        
        # Cập nhật current_order với dữ liệu mới
        current_order.update({
            "products": gemini_response.get("products", []),
            "productsError": gemini_response.get("productsError", []),
            "total_items": sum(p.get("quantity", 0) for p in gemini_response.get("products", [])),
            "updated_at": datetime.now().isoformat(),
            "message": gemini_response.get("message", "Đã cập nhật đơn hàng"),
            "spoken_text": text,
            "isLoading": False,
            "error": None
        })
        
        # Nếu created_at là None, set nó thành thời gian hiện tại
        if current_order.get("created_at") is None:
            current_order["created_at"] = datetime.now().isoformat()
            
        return current_order
            
    except Exception as e:
        error_logger = logging.getLogger('error_logger')
        error_logger.error(f"Lỗi xử lý Gemini: {str(e)}")
        error_logger.error(traceback.format_exc())
        return current_order

@app.route("/transcribe", methods=["POST"])
def handle_transcribe():
    start_time = time.time()
    audio_time = 0.0
    gemini_time = 0.0
    text = ""
    result = {"error": ""}
    current_time = datetime.now().strftime('%d-%m-%Y %H:%M:%S')
    response_size = 0
    
    try:
        # Lấy thông tin client
        client_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')
        
        if "audio" not in request.files:
            error_msg = f"Không tìm thấy file audio trong request từ client {client_ip}"
            logger.error(error_msg)
            return jsonify({"error": "No audio file provided"}), 400
            
        audio_file = request.files["audio"]
        if audio_file.filename == "":
            error_msg = f"File audio trống từ client {client_ip}"
            logger.error(error_msg)
            return jsonify({"error": "No selected file"}), 400
            
        # Xử lý audio
        audio_start_time = time.time()
        text = transcribe_audio(audio_file)
        audio_time = time.time() - audio_start_time
        
        # Xử lý text với Gemini
        gemini_start_time = time.time()
        result = process_with_gemini(text)
        gemini_time = time.time() - gemini_start_time
        
        # Tính thời gian xử lý tổng thể
        total_time = time.time() - start_time
        
        # Chuẩn bị dữ liệu request và response
        request_data = {
            "audio_file": audio_file.filename,
            "client_ip": client_ip,
            "text": text
        }
        
        response_data = {
            "spoken_text": text,
            "current_order": result
        }
        
        # Tính response size
        response_size = len(json.dumps(response_data, ensure_ascii=False))
        
        # Log theo format mới với thời gian xử lý Audio và Gemini
        log_message = f"{current_time}\t{handle_transcribe.__name__}\t/transcribe\tSUCCESS\tAudio: {audio_time:.3f}s - Gemini: {gemini_time:.3f}s - Tổng: {total_time:.3f}s\t{response_size}\t{user_agent}\t{json.dumps(request_data, ensure_ascii=False)}\tKết quả: {json.dumps(result, ensure_ascii=False)}"
        logger.info(log_message)
        
        return jsonify(response_data)
        
    except Exception as e:
        total_time = time.time() - start_time
        error_data = {"error": str(e), "client_ip": client_ip}
        
        # Log error message tổng quan vào server log
        error_msg = (
            f"{current_time}\t"
            f"handle_transcribe\t/transcribe\tERROR\t"
            f"Audio: {audio_time:.3f}s - Gemini: {gemini_time:.3f}s - "
            f"Tổng: {total_time:.3f}s\t{response_size}\t"
            f"{user_agent}\t"
            f"{json.dumps(error_data)}\t"
            f"Kết quả: {json.dumps(result)}"
        )
        logger.error(error_msg)
        
        # Log chi tiết lỗi vào error log
        error_logger = logging.getLogger('error_logger')
        error_logger.error(f"Chi tiết lỗi: {str(e)}")
        error_logger.error("Stack trace:")
        error_logger.error(traceback.format_exc())
        
        return jsonify(error_data), 500

@app.route("/reset_order", methods=["POST"])
def reset_order():
    try:
        start_time = time.time()
        logger.info("Bắt đầu reset đơn hàng")
        
        global current_order
        current_order = {
            "products": [],
            "productsError": [],
            "total_items": 0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "message": "",
            "spoken_text": ""
        }
        
        # Tính thời gian xử lý
        processing_time = time.time() - start_time
        
        logger.info(f"Reset đơn hàng thành công - Thời gian: {processing_time:.3f}s")
        return jsonify({"message": "Order reset successfully", "current_order": current_order})
        
    except Exception as e:
        logger.error(f"Lỗi khi reset đơn hàng: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Khởi tạo logging trước
    error_logger = setup_logging()
    
    try:
        # Code khởi động server
        logger.info("Server starting...")
        app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)
    except Exception as e:
        logger.error(f"Server failed to start: {str(e)}")
        logger.error(traceback.format_exc())