from playwright.sync_api import sync_playwright
from time import sleep
import json
import re
import os

def sanitize_filename(name):
    """Xóa các ký tự không hợp lệ khỏi chuỗi để tạo tên file hợp lệ."""
    # Xóa các ký tự không hợp lệ
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name)
    return sanitized

novel_url = "https://docln.sbs/sang-tac/15047-nguc-thanh"

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
                user_data_dir="./hako", 
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
    page = context.pages[0] if context.pages else context.new_page()

    print(f"Đang truy cập trang truyện: {novel_url}")
    page.goto(novel_url, timeout=60000, wait_until="domcontentloaded")

    # Lấy tiêu đề truyện để đặt tên file
    novel_title = page.locator("span.series-name a").inner_text()
    file_name = f"{sanitize_filename(novel_title)}.txt"
    print(f"Bắt đầu cào truyện: '{novel_title}'")

    # Lấy tất cả link và tiêu đề chương từ trang chính
    chapter_elements = page.locator("ul.list-chapters.at-series .chapter-name a").all()
    chapters_to_crawl = []
    for element in chapter_elements:
        href = element.get_attribute("href")
        title = element.inner_text()
        if href:
            chapters_to_crawl.append({"title": title, "url": "https://docln.sbs" + href})

    print(f"Tìm thấy tổng cộng {len(chapters_to_crawl)} chương.")

    # Mở file ở chế độ ghi nối (append) để lưu tiến trình
    with open(f"output/{file_name}", "a", encoding="utf-8") as f:
        # Ghi tiêu đề truyện vào đầu file nếu file mới
        if os.path.getsize(f"output/{file_name}") == 0:
            f.write(f"{novel_title.upper()}\n\n{'='*40}\n\n")

        for i, chapter in enumerate(chapters_to_crawl):
            print(f"Đang xử lý chương {i + 1}/{len(chapters_to_crawl)}: {chapter['title']}")
            try:
                page.goto(chapter['url'], timeout=60000, wait_until="domcontentloaded")

                # Đợi nội dung chương xuất hiện
                page.locator("#chapter-content").first.wait_for(timeout=30000)

                # Lấy tiêu đề và nội dung chính xác từ trang chương
                chapter_page_title = page.locator("h4.title-item").inner_text()
                content = page.locator("#chapter-content").inner_text() 

                # Ghi vào file
                f.write(f"{chapter_page_title.upper()}\n\n")
                f.write(content)
                f.write(f"\n\n{'='*40}\n\n")  # Thêm dấu phân cách giữa các chương

                print(f"-> Đã lưu chương: {chapter_page_title}")
                sleep(2)  # Tạm dừng 2 giây để tránh làm quá tải server
            except Exception as e:
                print(f"!!! Lỗi khi xử lý chương '{chapter['title']}': {e}")
                continue  # Bỏ qua chương lỗi và tiếp tục

print(f"\nHoàn tất! Đã lưu toàn bộ truyện vào file '{file_name}'.")