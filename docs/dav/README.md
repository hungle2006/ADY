# Bàn giao phần DAV

**Phạm vi.** Phần này lưu bản đóng góp DAV từ dự án chính Pharmacy_app, giữ cấu trúc đường dẫn để đối chiếu. Một DAV ID là một bản ghi kê khai. Một bản ghi có thể có nhiều thành phần nhưng chỉ có một giá kê khai cho sản phẩm trong dòng đó. Giá tính theo đơn vị ghi trong nguồn.

## Đọc file theo thứ tự

| File | Khi nào mở | Kết quả |
|---|---|---|
| [crawl_dav.py](../../pipeline/bronze/crawl_dav.py) | Cần lấy snapshot hoặc kiểm tra phân trang | JSONL raw, CSV trích cột và manifest tổng số dòng |
| [DAV.R](../../scripts/eda/DAV.R) và [DAV_ingredients_raw.R](../../scripts/eda/DAV_ingredients_raw.R) | Cần khảo sát trước chuẩn hóa | Biểu đồ phân phối giá, độ phủ, chuỗi hoạt chất và bảng nhóm giá |
| [dav_normalizer_v3.py](../../silver_v3/safe_tools/dav_normalizer_v3.py) | Cần tách thành phần và hàm lượng | Bảng product, component, QC và review queue |
| [normalize_davfull.py](../../scripts/dav/normalize_davfull.py) | Cần xuất bảng local cho SQL Server | Product, price_record, DDL và báo cáo chất lượng riêng |
| [Test parser DAV](../../tests/test_dav_ingredient_normalization.py) | Trước khi sửa hoặc commit quy tắc | Fixture nhỏ kiểm tra dấu phân tách, mẫu số, muối, UTF-16 và bảo toàn giá |

## Chạy từ một checkout ADY mới

Các lệnh PowerShell chạy từ gốc repo. Với Python 3.10 trở lên, kiểm tra đầu tiên chạy offline và chỉ dùng thư viện chuẩn.

```powershell
python -m unittest discover -s tests -p test_dav_ingredient_normalization.py -v
```

**Chọn input.** Nếu nhóm đã có CSV từ crawler hoặc DAVfull UTF-16/tab, đặt đường dẫn trong `$davCsv` và chuyển sang bước phân tích/chuẩn hóa. Nếu cần kiểm tra API hiện tại, chạy thử một lượt giới hạn 100 bản ghi vào thư mục riêng:

```powershell
python -m pip install -r scripts/dav/requirements.txt
$run = Join-Path 'work/dav' (Get-Date -Format 'yyyyMMdd-HHmmss')
python pipeline/bronze/crawl_dav.py --output-dir "$run" --max-rows 100
if ($LASTEXITCODE -ne 0) { throw 'Crawl DAV thất bại' }
$davCsv = (Get-ChildItem -LiteralPath $run -Filter 'dav_gia_ban_buon_*.csv' | Select-Object -First 1).FullName
```

Giới hạn 100 là chạy mẫu. Để lấy toàn bộ, tạo một thư mục lượt chạy mới, bỏ `--max-rows`, rồi đối chiếu `snapshot_complete`, `records_written` và `final_total_count` trong manifest cùng ID trùng/lỗi trang. `dav_declaration_date` trong crawler lấy từ `ngayTiepNhan`; ghi rõ ý nghĩa này khi vẽ chuỗi thời gian.

**EDA trước chuẩn hóa.** Cài `tidyverse` và `patchwork` một lần trong RStudio. Mở RStudio ở gốc ADY, thay đường dẫn đầu vào bên dưới bằng CSV của mình:

```r
Sys.setenv(PHARMACY_APP_ROOT = normalizePath("."), DAV_RAW_CSV = "duong/dan/toi/snapshot.csv")
if (.Platform$OS.type == "windows") Sys.setlocale("LC_CTYPE", ".UTF-8")
options(encoding = "UTF-8")
source("scripts/eda/DAV.R", encoding = "UTF-8")
```

Xem hai ảnh trong `reports/eda/dav/` và đọc `DAV_raw_price_context_coverage.csv` trước khi diễn giải chênh lệch giá. Một nhóm khảo sát giữ cùng biểu thức hoạt chất, hàm lượng, dạng, đơn vị, quy cách, loại giá và tháng tiếp nhận.

**Chuẩn hóa.** Với `$davCsv` là đường dẫn CSV thật và thư mục đầu ra riêng cho snapshot:

```powershell
python silver_v3/safe_tools/dav_normalizer_v3.py --dav "$davCsv" --output-dir "work/dav/normalized"
```

Đối chiếu `dav_product_normalized_v2.csv` với tập DAV ID đầu vào. Trong `dav_component_normalized_v2.csv`, dùng `dav_row_id + component_order` để truy thành phần và giữ nguyên giá ở cấp sản phẩm. Hậu tố v2 là tên output kế thừa của normalizer v3. Đọc `dav_normalization_qc_v2.csv` và review queue trước khi dùng khóa tên để mapping.

Nếu cần xuất cho SSMS, chạy riêng `python scripts/dav/normalize_davfull.py --input "$davCsv" --output-dir work/dav/ssms`. ID sản phẩm đánh số trong lần xuất local chỉ dùng trong bộ output đó.

## Phần đã làm và phần người sau có thể tiếp tục

- Crawler kiểm tra tổng dòng, xuất manifest; normalizer đọc UTF-8 CSV và UTF-16 TSV, giữ raw, chuẩn hóa đơn vị và đánh dấu biểu thức nhiều hàm lượng cần xem lại.
- Dấu `+` ngoài ngoặc có thể tách hai thành phần. Dấu `/` trong `IU/5ml` mô tả mẫu số. Khi tách, giữ giá của cả sản phẩm ở cấp DAV ID. Khóa tên tìm kiếm vẫn cần bảng từ điển và bằng chứng trước khi coi là hoạt chất đã duyệt.
- Snapshot local dùng để đối chiếu trước đây có 39.692 DAV ID. Giá trị này phụ thuộc lần thu. Trước khi dùng số đăng ký để nối giá qua nhiều ngày, rà soát các giá trị dạng số mũ với JSON gốc theo DAV ID.
- [Patch tích hợp Pharmacy_app](pharmacy-app-integration.patch) chứa phần thay đổi liên quan DAV của 5 file dùng chung: kiểm manifest trước Bronze, đưa component vào Silver, tính revision, dùng component trong mapping và thêm test vào CI. Patch có mốc **b4df4c9**. Chỉ áp dụng trong checkout đầy đủ của Pharmacy_app, sau khi chép các file DAV trên đúng đường dẫn. Chạy `git apply --check <đường-dẫn-patch>` trước; nếu các thay đổi đã có ở checkout hiện tại thì đối chiếu diff, tránh áp dụng hai lần. Bản đóng góp trong ADY chạy local, không tự kích hoạt pipeline R2/Gold của cả nhóm.

## Thành viên sau đóng góp thế nào?

Từ checkout riêng đã lưu công việc của mình:

```powershell
git switch main
git pull --ff-only
git switch -c feature/ten-phan
# Sửa phần của bạn, thêm hướng dẫn đầu vào/đầu ra và chạy kiểm tra liên quan.
git status --short
# Thay đường dẫn ví dụ bằng đúng file của phần mình.
git add -- duong/dan/file-cua-ban
git diff --cached --stat
git commit -m "Mo ta thay doi cua ban"
git push -u origin feature/ten-phan
```

Mở pull request vào `main` để nhóm review. Trong mô tả ghi đầu vào mẫu, câu lệnh chạy, đầu ra và giới hạn. File sinh từ từng snapshot nên nằm trong `work/dav/` hoặc `reports/eda/dav/`; source và test mới là phần cần theo dõi trong Git.
