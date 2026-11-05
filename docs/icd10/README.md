# ICD-10 Việt Nam

Chạy từ thư mục gốc repo với Python 3.10+ và thư viện `requests`. R dùng `tidyverse` và `patchwork`.

1. **Thu thập**. Crawler duyệt các chương và các node con cho từng ngôn ngữ, nối tiếng Việt và tiếng Anh theo cặp `(model, id)`. Cặp này phân biệt được hai node có cùng ID ở hai cấp. Chạy mỗi lần trong một thư mục mới.

   ```powershell
   $run = "work/icd10/" + (Get-Date -Format yyyyMMdd-HHmmss)
   python -X utf8 pipeline/bronze/crawl_icd10.py --output-dir "$run/raw"
   ```

2. **Khảo sát trước chuẩn hóa**. Mở RStudio ở thư mục gốc hoặc chạy lệnh dưới đây. Biểu đồ thể hiện số dòng theo cấp, trường trống, độ dài tên và dấu trong mã. Với dữ liệu cây, số dòng bao gồm cả các chương và nhóm bệnh.

   ```powershell
   $env:ICD10_RAW_CSV = "$run/raw/ICD10_nodes_bilingual.csv"
   Rscript scripts/eda/ICD10_raw.R
   ```

3. **Chuẩn hóa**. Bảng cây cần cột `parent_model` để lần ngược từ bệnh lên cấp cha. Công cụ cũng nhận bản xuất phẳng CSV, TSV hoặc XLSX có `icd_code`, `code_level`, `disease_name_vi`. Với cây, chỉ xuất dòng cấp `type` và `disease`. Với bản phẳng, giữ từng dòng nguồn. Mã gốc và ký hiệu được giữ để đối chiếu. Mã chuẩn có thể trùng nên cần xem cấp và mã gốc trước khi dùng làm khóa nối.

   ```powershell
   python -X utf8 silver_v3/safe_tools/normalize_icd10_vietnam.py --input "$run/raw/ICD10_nodes_bilingual.csv" --output-dir "$run/normalized"
   ```

4. **Kiểm tra**. Đọc `icd10_crawl_manifest.json` trong thư mục raw để xem số node từng ngôn ngữ, số gốc và node chỉ có ở một ngôn ngữ. Đọc `ICD10_Vietnam_normalization_qc.csv` và `ICD10_canonical_collisions.csv` trong thư mục normalized để kiểm tra số dòng giữ lại và các mã cần phân biệt. Kết quả là danh mục ICD cho bước nối sau. Việc tạo quan hệ bệnh–thuốc cần thêm dữ liệu ánh xạ.

Kiểm thử offline từ thư mục gốc:

```powershell
python -X utf8 -m unittest discover -s tests -p test_icd10_pipeline.py
```

Đưa phần ICD lên nhánh riêng bằng cách stage đúng crawler, normalizer, script R, bài kiểm thử và README này. Các thư mục `work/` và `reports/` chứa kết quả chạy trên máy. Pipeline ở đây được chạy thủ công.
