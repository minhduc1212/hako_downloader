from playwright.sync_api import sync_playwright
from time import sleep
import json

base_url = "https://docln.sbs/danh-sach?truyendich=1&sangtac=1&convert=1&dangtienhanh=1&tamngung=1&hoanthanh=1&sapxep=tentruyen&page="
url_list = []

# Đọc các URL đã có để không bị ghi đè và tránh trùng lặp
try:
    with open("url_list.txt", "r", encoding="utf-8") as f:
        url_list = json.load(f)
    print(f"Đã tải {len(url_list)} URL từ file đã có.")
except (FileNotFoundError, json.JSONDecodeError):
    print("File url_list.txt không tồn tại hoặc rỗng. Bắt đầu từ đầu.")
    url_list = []

# Dùng set để kiểm tra trùng lặp nhanh hơn
existing_urls = set(url_list)


ITEMS_PER_PAGE = 42 
start_page = (len(existing_urls) // ITEMS_PER_PAGE) + 1
current_page = start_page

if current_page > 1:
    print(f"Đã có {len(existing_urls)} URL. Tự động tiếp tục cào từ trang {current_page}.")


with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
                user_data_dir="./hako", 
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
    page = context.pages[0] if context.pages else context.new_page()

    while True:
        target_url = f"{base_url}{current_page}"
        print(f"Đang xử lý trang {current_page}: {target_url}")
        try:
            # Tối ưu: Chỉ đợi HTML tải xong, không cần đợi tất cả tài nguyên (ảnh, script,...)
            page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
            page.locator("div.thumb_attr.series-title a").first.wait_for(timeout=30000)

        except Exception as e: # Bắt lỗi nếu trang không tải được hoặc không tìm thấy truyện
            print(f"Lỗi khi tải trang {current_page}: {e}")
            print("Bỏ qua trang này và chuyển sang trang tiếp theo.")
            current_page += 1
            continue
        
        #get novel urls in page
        a_list = page.locator("div.thumb_attr.series-title a").all()
        if not a_list:
            print("Không tìm thấy truyện nào trên trang này. Kết thúc quá trình cào dữ liệu.")
            break
        for a in a_list:
            href = a.get_attribute("href")
            if href:
                full_url = "https://docln.sbs" + href
                if full_url not in existing_urls:
                    url_list.append(full_url)
                    existing_urls.add(full_url)
        
        print(f"Tổng số URL hiện tại: {len(url_list)}")
        with open("url_list.txt", "w", encoding="utf-8") as f:
            json.dump(url_list, f, ensure_ascii=False, indent=4)
        print(f"Done page{current_page}!")

        # Chuyển sang trang tiếp theo
        current_page += 1

        sleep(5)  # Tạm dừng 5 giây giữa các trang để tránh bị chặn

print("Hoàn tất!")