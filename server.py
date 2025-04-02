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
    return f"""
    Câu hỏi: {question}
    
    QUY TẮC VÀNG - PHẢI TUÂN THỦ TUYỆT ĐỐI:
    1. KHÔNG ĐƯỢC TỰ Ý CHUYỂN ĐỔI TÊN SẢN PHẨM
    2. KHÔNG DÙNG TRỌNG LƯỢNG ĐỂ ĐOÁN TÊN
    3. PHẢI KHỚP 100% TỪ KHÓA THEO MENU
    4. CHỈ LẤY SỐ LƯỢNG KHI CÓ TỪ "CÁI"
    5. KHÔNG ĐƯỢC ĐOÁN TỪ TƯƠNG TỰ
    6. NẾU KHÔNG KHỚP -> BỎ QUA HOÀN TOÀN
    
    QUY TẮC NHẬN DIỆN TỪ KHÓA:
    1. Bánh bao hoa hồng:
       - PHẢI có chính xác "hoa hồng"
       - KHÔNG chấp nhận: "hoa cúc", "hoa mai", "bông hồng",...

    2. Bánh bao cua xanh:
       - Chấp nhận: "cua xanh" hoặc "cao su"
       - PHẢI đi kèm "vani" và "260g"

    [các sản phẩm khác...]

    VÍ DỤ PHÂN TÍCH:
    Input: "bao hoa cúc 5 cái"
    
    Phân tích:
    - Tìm thấy "hoa cúc"
    - "hoa cúc" ≠ "hoa hồng"
    - KHÔNG ĐƯỢC thay thế "cúc" bằng "hồng"
    -> BỎ QUA vì không khớp từ khóa menu

    QUY TẮC NHẬN DIỆN VÀ XỬ LÝ:
    1. TÁCH CÂU:
       - Tách khi gặp từ bắt đầu: "bao", "bánh", "xíu", "hamburger", "giò", "cảo", "hoành", "xôi", "đào", "dorayaki"
       - Tách khi gặp số lượng: "X cái"
       - Tách khi gặp dấu phẩy hoặc từ nối

    2. TỪ ĐỒNG NGHĨA ĐƯỢC CHẤP NHẬN:
       PHÁT ÂM SẢN PHẨM:
       - "cao su/venix" = "cua xanh"
       - "theo" = "heo"
       - "khúc/phút" = "cút"
       - "sứ mệnh/khuyến mại/nếu mái" = "xíu mại"
       - "tôn" = "tôm"
       - "bì hương" = "mỹ hương"
       
       PHÁT ÂM SỐ:
       - "một/mốt" = 1
       - "hai" = 2
       - "ba" = 3
       - "bốn" = 4
       - "năm" = 5
       - "sáu" = 6
       - "bảy" = 7
       - "tám" = 8
       - "chín" = 9
       - "mười" = 10

    3. TỪ KHÓA THEO NHÓM SẢN PHẨM:
       PHỤ KIỆN:
       - Hộp giấy: hộp + giấy
       - Túi giấy bánh bao: túi + giấy
       - Túi Hamburger: túi + hamburger
       - Đĩa kết đào: đĩa + (nhỏ/trung/lớn) + (15/17/21)cm
       - Mâm kết đào: mâm + (nhỏ/trung/lớn) + (25/30/35)cm

       BÁNH GIÒ:
       - Giò gà: giò + gà + 1 cút + 150g
       - Giò heo: giò + heo + (1/2) cút + (150g/200g)

       BÁNH BAO THỌ PHÁT:
       - Bí đỏ: bí đỏ + sữa + 280g
       - Cade: cade + (240g/280g)
       - Đậu xanh: đậu xanh + (200g/280g/300g/400g)
       - Hoàng kim: hoàng kim + 300g
       - Khoai môn: khoai môn + (200g/280g)
       - Than tre: than tre + phô mai + 300g
       - Cua xanh: (cua xanh/cao su) + vani + 260g
       - Cuộn màu: cuộn + (hồng/môn/cacao/vàng/xanh) + không nhân + (300g/500g)
       - Vuông: vuông + không nhân + (300g/264g)
       - Hoa hồng: hoa hồng + không nhân + (300g/264g)
       - Bánh kẹp: bánh kẹp + 520g
       - Tạo hình: 
         + con + (ong/gà/gấu/heo/nhím) + (trứng sữa/lá dứa) + 200g
         + con gấu + socola + 200g
         + người tuyết + socola + 240g
         + trái tim + socola + valentine + 200g
       - Chay: 
         + chay + 400g
         + chay đặc biệt + 640g
         + chay + đậu hũ + sả + 400g
       - Heo: 
         + heo + (1/2/7) cút + (có/không muối) + trọng lượng
         + heo đặc biệt + 1 cút + muối + 780g
       - Thập cẩm: thập cẩm + (1/2) cút + (có/không muối) + trọng lượng
       - Xá xíu: xá xíu + (phô mai/không) + (280g/480g)
       - Bò pizza: bò + pizza + phô mai + 480g
       - Xúc xích: xúc xích + phô mai + 400g
       - Gà nướng: gà nướng + phô mai + 400g
       - Phát tài: phát tài + 240g
       - Dừa: 
         + dừa + thốt nốt + trân châu + 200g
         + cơm dừa + 200g

       BÁNH BAO MỸ HƯƠNG:
       - Bí đỏ: mỹ hương + bí đỏ + sữa + (300g/264g)
       - Vịt quay: mỹ hương + vịt quay + tiêu đen + 280g
       - Heo: mỹ hương + heo + (1/2/3) cút + (có/không muối) + trọng lượng
       - Thập cẩm: mỹ hương + thập cẩm + 2 cút + 520g

       BÁNH BÔNG LAN:
       - Bông lan phô mai: bông lan + phô mai + 90g

       HÁ CẢO:
       - Cảo heo: cảo + heo + (500g/600g)
       - Cảo tôm: cảo + tôm + 600g

       HOÀNH THÁNH:
       - Hoành thánh heo: hoành thánh + heo + (500g/480g)
       - Hoành thánh tôm: hoành thánh + tôm + 400g

       XÍU MẠI:
       - Xíu mại heo: xíu mại + heo + 500g
       - Xíu mại tôm: xíu mại + tôm + 600g
       - Xíu mại thịt viên: xíu mại + thịt viên + 600g

       XÔI:
       - Xôi gà: xôi + gà + nấm đông cô + 160g

       HAMBURGER:
       - Hamburger bò: hamburger + bò + 650g
       - Hamburger gà: hamburger + gà + 720g
       - Hamburger heo: hamburger + heo + 800g
       - Hamburger tôm: hamburger + tôm + 700g
       - Vỏ hamburger: vỏ + bánh + hamburger

       BÁNH TRÁI ĐÀO:
       - Đào đậu xanh: đào + đậu xanh + 70g
       - Đào khoai môn: đào + khoai môn + 70g

       BÁNH DORAYAKI:
       - Dorayaki chà bông: dorayaki + chà bông + bơ + 50g
       - Dorayaki socola: dorayaki + socola + 50g

    4. QUY TẮC XỬ LÝ:
       TRỌNG LƯỢNG:
       - PHẢI khớp chính xác với menu
       - Chấp nhận "g" hoặc "gam"
       - KHÔNG dùng để đoán tên sản phẩm
       
       SỐ LƯỢNG - QUY TẮC NGHIÊM NGẶT:
       a) CHỈ lấy số trong các trường hợp:
          1. Số đi kèm từ "cái":
             - "X cái" -> số lượng = X
             - "cái thứ X" -> số lượng = X
          2. Nếu không tìm thấy số lượng -> mặc định = 1
          
       b) Ưu tiên số gần nhất với từ "cái":
          ✓ ĐÚNG:
          - "5 cái" -> số lượng = 5
          - "cái thứ năm" -> số lượng = 5
          - "bao hoa hồng 264g 6 cái" -> số lượng = 6
          
          ✗ SAI:
          - "5 bao" -> không lấy số lượng
          - "3 cút" -> không phải số lượng
          - "600g" -> không phải số lượng
          - "600 g" -> không phải số lượng

    5. VÍ DỤ PHÂN TÍCH:
    Input: "bao cao su venix 260g 3 cái bao mỹ hương theo một khúc muối 640g ba cái bao bì hương thập cẩm hai khúc 520g vậy cái"
    
    Phân tích:
    1) "bao hoa hồng 264g"
       - Từ khóa: hoa hồng, không nhân, 264g ✓
       - Không thấy số lượng -> mặc định = 1 ✓
       -> CHẤP NHẬN: Bánh Bao Thọ Phát Không Nhân Hoa Hồng
    
    2) "xúc xích phô mai 400g 5 cái"
       - Từ khóa: xúc xích, phô mai, 400g ✓
       - Số lượng: "5 cái" = 5 ✓
       -> CHẤP NHẬN: Bánh Bao Thọ Phát Xúc Xích Phô Mai
       
    3) "bao cao su venix 260g 3 cái"
       - "venix" = "vani" ✓
       - cao su = cua xanh ✓
       - Từ khóa: cua xanh, vani, 260g ✓
       - Số lượng: "3 cái" = 3 ✓
       -> CHẤP NHẬN: Bánh Bao Thọ Phát KN Cua Xanh Vani
    
    4) "bao mỹ hương theo một khúc muối 640g ba cái"
       - "theo" = "heo" ✓
       - "khúc" = "cút" ✓
       - "một" = 1 (trong mô tả sản phẩm) ✓
       - Từ khóa: mỹ hương, heo, 1 cút, muối, 640g ✓
       - Số lượng: "ba cái" = 3 ✓
       -> CHẤP NHẬN: Bánh Bao Mỹ Hương Thịt Heo 1C1M

    5) "bao bì hương thập cẩm hai khúc 520g vậy cái"
       - "bì hương" = "mỹ hương" ✓
       - "khúc" = "cút" ✓
       - "vậy" = "bảy" ✓
       - Từ khóa: mỹ hương, thập cẩm, 2 cút, 520g ✓
       - Số lượng: "bảy cái" = 7 ✓
       -> CHẤP NHẬN: Bánh bao Mỹ Hương Thập cẩm 2C

    MENU CHÍNH:
    {get_menu()}

    Trả về JSON với cấu trúc:
    {{
        "products": [
            {{
                "name": "Tên đầy đủ từ menu",
                "quantity": số_lượng_từ_cái,
                "sapCode": "mã_sản_phẩm",
                "description": "Khớp 100% với yêu cầu: <từ khóa khớp>"
            }}
        ]
    }}

    LƯU Ý CUỐI CÙNG:
    1. TUYỆT ĐỐI KHÔNG được chuyển đổi tên sản phẩm
    2. TUYỆT ĐỐI KHÔNG dùng trọng lượng để đoán tên
    3. CHỈ chấp nhận khi khớp 100% từ khóa
    4. CHỈ lấy số lượng khi có từ "cái"
    5. PHẢI lấy đúng số lượng gần với từ "cái" nhất
    6. Nếu không khớp -> BỎ QUA hoàn toàn
    """

def transcribe_audio(audio_data):
    try:
        # Chuyển đổi WebM sang WAV
        audio = AudioSegment.from_file(audio_data, format="webm")
        audio = audio.set_channels(1)
        audio = audio.set_frame_rate(16000)
        
        # Lưu file WAV tạm thời
        temp_wav = "temp_audio.wav"
        audio.export(temp_wav, format="wav")
        
        # Điều chỉnh nhiễu
        audio = AudioSegment.from_wav(temp_wav)
        audio = audio - 10  # Giảm âm lượng 10dB
        
        # Lưu file đã điều chỉnh
        audio.export(temp_wav, format="wav")
        
        # Nhận dạng giọng nói
        recognizer = sr.Recognizer()
        with sr.AudioFile(temp_wav) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="vi-VN")
        
        # Xóa file tạm
        os.remove(temp_wav)
        
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
            "total_items": sum(p.get("quantity", 0) for p in gemini_response.get("products", [])),
            "updated_at": datetime.now().isoformat(),
            "message": gemini_response.get("message", "Đã cập nhật đơn hàng"),
            "spoken_text": text
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
        app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)
    except Exception as e:
        logger.error(f"Server failed to start: {str(e)}")
        logger.error(traceback.format_exc())