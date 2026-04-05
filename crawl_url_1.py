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

# Yêu cầu người dùng nhập số trang cụ thể
try:
    page_to_crawl = int(input("Nhập số trang bạn muốn cào: "))
    if page_to_crawl <= 0:
        raise ValueError("Số trang phải là một số nguyên dương.")
except ValueError as e:
    print(f"Lỗi: {e}. Vui lòng nhập một số nguyên dương.")
    exit()

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
                user_data_dir="./hako", 
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
    page = context.pages[0] if context.pages else context.new_page()

    target_url = f"{base_url}{page_to_crawl}"
    print(f"Đang xử lý trang {page_to_crawl}: {target_url}")
    try:
        # Tối ưu: Chỉ đợi HTML tải xong, không cần đợi tất cả tài nguyên (ảnh, script,...)
        page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        # Sau đó, đợi cho đến khi phần tử đầu tiên trong danh sách truyện xuất hiện.
        # Cách này nhanh hơn 'networkidle' và đáng tin cậy hơn 'sleep'.
        page.locator("div.thumb_attr.series-title a").first.wait_for(timeout=30000)

    except Exception as e: # Bắt lỗi nếu trang không tải được hoặc không tìm thấy truyện
        print(f"Lỗi khi tải trang {page_to_crawl}: {e}")
        print("Không thể xử lý trang. Kết thúc chương trình.")
        exit()
    
    #get novel urls in page
    a_list = page.locator("div.thumb_attr.series-title a").all()
    if not a_list:
        print("Không tìm thấy truyện nào trên trang này.")
    else:
        new_urls_found = 0
        for a in a_list:
            href = a.get_attribute("href")
            if href:
                full_url = "https://docln.sbs" + href
                if full_url not in existing_urls:
                    url_list.append(full_url)
                    existing_urls.add(full_url)
                    new_urls_found += 1
        
        print(f"Đã tìm thấy {new_urls_found} URL mới trên trang {page_to_crawl}.")
        print(f"Tổng số URL hiện tại: {len(url_list)}")
        with open("url_list.txt", "w", encoding="utf-8") as f:
            json.dump(sorted(list(existing_urls)), f, ensure_ascii=False, indent=4)
        print(f"Đã lưu thành công vào url_list.txt!")

print("\nHoàn tất!")