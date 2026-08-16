# 🚀 Hako / Docln Crawler System (v2.0.0)

Hệ thống crawl light novel chuyên nghiệp từ **Docln (Hako)** với cơ chế **Tự động Daily Sync**, **SQLite Database chuẩn hóa**, **Anti-Bot / Proxy / Tor**, **Adaptive Rate Limiting**, **Dead-Letter Post-Retry Queue** và **Rich UI Logging**.

---

## 📑 Mục Lục
1. [Tính Năng Chính](#-tính-năng-chính)
2. [Cấu Trúc Thư Mục & Module](#-cấu-trúc-thư-mục--module)
3. [Cơ Sở Dữ Liệu (SQLite Schema)](#-cơ-sở-dữ-liệu-sqlite-schema)
4. [Cài Đặt & Cấu Hình](#-cài-đặt--cấu-hình)
5. [Hướng Dẫn Sử Dụng (CLI Commands)](#-hướng-dẫn-sử-dụng-cli-commands)
   - [1. Daily Sync (Cập nhật hàng ngày)](#1-daily-sync-luôn-up-to-date)
   - [2. Daemon Scheduler (Chạy nền định kỳ)](#2-daemon-scheduler-chạy-tự-động)
   - [3. Manual Crawl (Cào truyện chỉ định)](#3-manual-crawl-cào-truyện-lẻ)
   - [4. Recrawl (Cào lại / Đồng bộ chương thiếu)](#4-recrawl-cào-lại-và-làm-mới)
   - [5. Post-Retry (Thử lại các mục lỗi)](#5-post-retry-dead-letter-queue)
   - [6. Export (.txt & .md)](#6-export-xuất-file-truyện)
   - [7. Stats & Dashboard](#7-stats-thống-kê-database)
   - [8. Test Proxy / Tor](#8-test-proxy--tor)
6. [Cơ Chế Chống Ban & Bảo Vệ](#-cơ-chế-chống-ban--bảo-vệ)

---

## 🌟 Tính Năng Chính

- 🔄 **Daily Auto-Sync**: Tự động quét danh mục "Mới cập nhật", so khớp DB và chỉ tải về các chương mới/truyện mới.
- ⏰ **Daemon Scheduler**: Chế độ chạy ngầm định kỳ (ví dụ mỗi 6 tiếng) liên tục 24/7.
- 🗄️ **SQLite WAL Mode**: Lưu trữ đầy đủ chi tiết: Thông tin truyện, danh sách tập (Volume), chi tiết từng chương (Chapter), nội dung sạch (đã lọc rác/Shopee banner), hình ảnh minh họa và ảnh bìa.
- 🖼️ **Media Downloader**: Tự động tải ảnh bìa và tất cả ảnh minh họa trong chương về máy cục bộ, tránh lỗi link ảnh chết.
- 🛡️ **Anti-Fingerprint & Proxy/Tor**: Hỗ trợ SOCKS5 (Tor 127.0.0.1:9050), HTTP Proxy xoay vòng, che giấu `navigator.webdriver`, User-Agent pool hiện đại.
- ⚡ **Adaptive Rate Limiter**: Token-bucket tự giảm tốc độ khi gặp HTTP 429 và kích hoạt backoff toàn cục, tự phục hồi tốc độ khi ổn định.
- 🔁 **Dead-Letter Post-Retry**: Tự động thu thập các chương/ảnh bị lỗi kết nối trong phiên và thử lại ở pha Post-Retry cuối cùng.
- 📊 **Rich Logging**: Giao diện console trực quan, màu sắc rõ ràng kèm Rotating File Log (`logs/crawler.log`).

---

## 📂 Cấu Trúc Thư Mục & Module

```
D:\LT\Crawl_Docln\
├── config/
│   ├── config.yaml          # File cấu hình trung tâm (tốc độ, workers, proxy, database...)
│   └── proxies.txt          # Danh sách proxy (nếu dùng proxy pool)
├── src/
│   ├── config.py            # Quản lý cài đặt & nạp YAML
│   ├── database/
│   │   ├── models.py        # Dataclass: Novel, Volume, Chapter, Image, CrawlLog, RetryItem
│   │   ├── connection.py    # Kết nối SQLite WAL mode, context manager an toàn
│   │   └── repository.py    # CRUD: Upsert novel, volume, chapter, image, retry queue, stats
│   ├── core/
│   │   ├── browser_manager.py # Quản lý Playwright context, stealth evasions, chặn font/ads
│   │   ├── proxy_manager.py   # Quản lý SOCKS5, Tor, HTTP proxy rotation
│   │   ├── rate_limiter.py    # Token-bucket rate limiter, random delay + Gaussian jitter
│   │   └── retry_manager.py   # Retry decorator & Post-Retry Worker (Dead-Letter Queue)
│   ├── parsers/
│   │   ├── novel_parser.py    # Parse metadata truyện, tác giả, thể loại, volumes, chapters
│   │   ├── chapter_parser.py  # Parse nội dung chương, lọc ads Shopee, bóc tách ảnh minh họa
│   │   └── feed_parser.py     # Parse trang 'Mới cập nhật' và danh mục truyện
│   ├── crawler/
│   │   ├── media_crawler.py   # Tải ảnh bìa & ảnh minh họa chương bất đồng bộ
│   │   ├── novel_crawler.py   # Điều phối cào 1 truyện, so khớp chương đã có trong DB
│   │   ├── engine.py          # Multi-worker async engine
│   │   ├── daily_crawler.py   # Engine đồng bộ hàng ngày (Daily Sync Engine)
│   │   └── scheduler.py       # Daemon scheduler chạy định kỳ
│   ├── cli/
│   │   └── commands.py        # Các command handlers của CLI
│   └── utils/
│       ├── logger.py          # Rich Console logger & Rotating File Handler
│       ├── helpers.py         # Hàm phụ trợ (sanitize tên file, format bytes, slug)
│       └── exporter.py        # Xuất truyện từ DB ra TXT và Markdown
├── data/
│   └── hako.db              # Database SQLite chính
├── output/
│   ├── media/
│   │   ├── covers/          # Lưu ảnh bìa các truyện
│   │   └── chapters/        # Lưu ảnh minh họa theo từng slug truyện
│   └── novels/              # Thư mục xuất file .txt / .md
├── logs/
│   └── crawler.log          # File log ghi chi tiết
├── main.py                  # Điểm khởi chạy CLI chính
└── README.md
```

---

## 🗄️ Cơ Sở Dữ Liệu (SQLite Schema)

Database SQLite đặt tại `data/hako.db` sử dụng chế độ **WAL (Write-Ahead Logging)** cho tốc độ cao và không bị lock:

1. **`novels`**: Lưu thông tin chi tiết từng truyện (tiêu đề, tên khác, tác giả, họa sĩ, thể loại JSON, tóm tắt, ảnh bìa gốc + đường dẫn cục bộ, số từ, lượt xem, đánh giá, trạng thái cào).
2. **`volumes`**: Quản lý từng Tập / Quyển của truyện.
3. **`chapters`**: Lưu chi tiết từng chương (tiêu đề, index, ngày đăng, số từ, nội dung text đã làm sạch, html gốc, danh sách link ảnh trong chương JSON, trạng thái).
4. **`images`**: Quản lý tất cả ảnh bìa và ảnh minh họa trong chương, trạng thái tải về (`downloaded`, `failed`) và dung lượng file.
5. **`crawl_logs`**: Nhật ký các phiên crawl (số truyện quét, số truyện cập nhật, số chương mới, thời gian chạy, lỗi).
6. **`retry_queue`**: Hàng đợi Dead-Letter Queue lưu lại các mục lỗi để thử lại an toàn.

---

## ⚙️ Cài Đặt & Cấu Hình

### 1. Cài đặt môi trường
Hệ thống chạy trên Python 3.10+ (Đã cài đặt sẵn trong `.venv`):
```bash
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
playwright install chromium
```

### 2. Tùy chỉnh `config/config.yaml`
```yaml
crawler:
  base_url: "https://docln.sbs"
  headless: true
  num_workers: 3
  
  # Adaptive Rate Limiting
  max_rps: 2.5
  chapter_delay_min: 0.8
  chapter_delay_max: 2.0
  random_jitter: true
  
  # Backoff khi bị 429
  backoff_429_min: 60.0
  backoff_429_max: 120.0
  max_retries: 3
  
media:
  download_images: true
  download_covers: true
  download_chapter_illustrations: true

proxy:
  enabled: false
  proxy_type: "socks5" # socks5, http, tor
  proxy_url: "socks5://127.0.0.1:9050" # Cổng mặc định của Tor Browser / Tor Service
```

---

## 💻 Hướng Dẫn Sử Dụng (CLI Commands)

### 1. Cào toàn bộ trang web (Crawl All / Bootstrap)
Quét toàn bộ danh mục website (tất cả ~114 trang / ~4,700 truyện), lưu vào SQLite và tự động tải toàn bộ chương cùng hình ảnh:
```bash
# Cào toàn bộ danh mục từ trang 1 đến trang cuối
python main.py crawl-all

# Cào từ trang 1 đến trang 10 với 5 workers
python main.py crawl-all --start-page 1 --end-page 10 --workers 5

# Chỉ quét và lập chỉ mục (index) danh sách truyện vào SQLite mà chưa tải chương
python main.py discover
```

> **Ghi chú Khả năng Resume:** Nếu quá trình cào bị gián đoạn (tắt máy/mất mạng), bạn chỉ cần chạy lại lệnh `python main.py crawl-all`, hệ thống sẽ tự động bỏ qua những truyện đã hoàn thành và tiếp tục cào các truyện/chương còn thiếu.

---

### 2. Daily Sync (Luôn Up-To-Date sau khi đã cào toàn bộ)
Sau khi đã cào dữ liệu gốc, mỗi ngày chỉ cần chạy `daily`, hệ thống sẽ chỉ quét bảng tin "Mới cập nhật", so khớp DB và **chỉ tải các chương mới ra / truyện mới**:
```bash
# Quét 5 trang cập nhật mới nhất (mặc định)
python main.py daily

# Quét 2 trang đầu
python main.py daily --pages 2
```

---

### 3. Daemon Scheduler (Tự động chạy ngầm 24/7)
Chạy chế độ daemon liên tục 24/7, tự động đồng bộ theo khoảng thời gian đặt trước (ví dụ mỗi 6 tiếng):
```bash
# Chạy đồng bộ mỗi 6 tiếng một lần
python main.py schedule --interval 6

# Bỏ qua lần chạy đầu tiên ngay khi mở
python main.py schedule --interval 6 --no-immediate
```

---

### 4. Manual Crawl (Cào truyện lẻ)
Cào 1 truyện chỉ định bằng URL hoặc Slug:
```bash
python main.py crawl --url "https://docln.sbs/truyen/28089-may-man-khi-gap-xui-xeo"
```

---

### 5. Recrawl (Cào lại và làm mới)
Cào lại toàn bộ hoặc bổ sung các chương bị thiếu:
```bash
# Cào lại 1 truyện
python main.py recrawl --url "https://docln.sbs/truyen/28089-may-man-khi-gap-xui-xeo"

# Kiểm tra và cập nhật lại toàn bộ các truyện đã có trong DB
python main.py recrawl-all
```

---

### 6. Post-Retry (Dead-Letter Queue)
Chạy worker xử lý các mục (chương/ảnh) bị lỗi kết nối trước đó:
```bash
python main.py retry-failed
```

---

### 7. Export (Xuất file truyện)
Xuất truyện đã lưu trong SQLite ra định dạng TXT hoặc Markdown:
```bash
# Xuất ra TXT
python main.py export --id 1

# Xuất ra Markdown (kèm link ảnh bìa và minh họa)
python main.py export --id 1 --format md

# Xuất theo URL truyện
python main.py export --url "https://docln.sbs/truyen/28089-may-man-khi-gap-xui-xeo" --format md
```

---

### 8. Stats (Thống kê Database)
Xem tổng số truyện, số volume, số chương, ảnh tải về và lịch sử crawl gần nhất:
```bash
python main.py stats
```

---

### 9. Test Proxy / Tor
Kiểm tra kết nối Proxy / Tor và hiển thị IP đang sử dụng:
```bash
python main.py test-proxy
```

---

## 🛡️ Cơ Chế Chống Ban & Bảo Vệ

1. **Tor / Proxy Integration**: Hỗ trợ kết nối qua Tor Daemon (`socks5://127.0.0.1:9050`) hoặc Proxy xoay vòng.
2. **Stealth Browser Args**: Tự động gỡ bỏ cờ `navigator.webdriver`, bổ sung ngôn ngữ và plugin trình duyệt tự nhiên.
3. **Adaptive Token-Bucket Limiter**: Tự động giảm tốc độ khi máy chủ phản hồi 429 và tạm dừng tất cả workers an toàn trong thời gian backoff.
4. **Gaussian Jitter**: Độ trễ ngẫu nhiên giữa các chương và trang tránh bị phân tích hành vi bot định kỳ.
5. **Ad & Duplicate Filter**: Tự động lọc sạch quảng cáo Shopee/Lazada và các banner không mong muốn trong nội dung chương.
