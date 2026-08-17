import requests
import json
import base64
from bs4 import BeautifulSoup

def decrypt_hako_chapter(data_c_json, data_k):
    # Chuyển chuỗi JSON thành list các phân đoạn
    chunks = json.loads(data_c_json)
    
    # Sắp xếp các phân đoạn theo 4 ký tự đầu (khử Shuffle)
    chunks.sort(key=lambda x: int(x[:4]))
    
    decrypted_text = ""
    key_length = len(data_k)
    
    for chunk in chunks:
        # Bỏ 4 ký tự index ở đầu mỗi đoạn
        encoded_data = chunk[4:]
        
        # Giải mã Base64 thành mảng bytes
        decoded_bytes = base64.b64decode(encoded_data)
        
        # Giải mã XOR
        decrypted_bytes = bytearray()
        for i, byte in enumerate(decoded_bytes):
            # Lấy ký tự khóa tương ứng theo vị trí
            key_char = data_k[i % key_length]
            decrypted_byte = byte ^ ord(key_char)
            decrypted_bytes.append(decrypted_byte)
        
        # Decode từ bytes sang chuỗi UTF-8 và nối vào kết quả
        decrypted_text += decrypted_bytes.decode('utf-8')
        
    return decrypted_text

# 1. Khởi tạo kết nối và lấy HTML thô
url = "https://docln.sbs/truyen/18973-childhood-friend-of-the-zenith/c144369-bia-novel"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
}

response = requests.get(url, headers=headers)
with open ("raw.html", "w", encoding="utf-8") as file:
    file.write(response.text)
print(response.status_code)
# 2. Sử dụng BeautifulSoup để tìm thẻ chứa dữ liệu mã hóa
soup = BeautifulSoup(response.text, "html.parser")
chapter_div = soup.find("div", id="chapter-c-protected") # Tìm div chứa nội dung[cite: 1]

if chapter_div:
    # Trích xuất data-c và data-k[cite: 1]
    data_c = chapter_div.get("data-c")
    data_k = chapter_div.get("data-k")
    
    if data_c and data_k:
        # 3. Tiến hành giải mã
        print("Đang giải mã nội dung...")
        decrypted_html = decrypt_hako_chapter(data_c, data_k)
        
        # 4. Lưu kết quả ra file
        with open("output.html", "w", encoding="utf-8") as file:
            file.write(decrypted_html)
        print("Giải mã thành công! Đã lưu vào file output.html")
    else:
        print("Không tìm thấy dữ liệu giải mã (data-c hoặc data-k) trong thẻ div.")
else:
    print("Không tìm thấy thẻ div chứa dữ liệu chương truyện.")