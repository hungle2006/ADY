library(tidyverse)
library(patchwork)
project_root <- Sys.getenv("PHARMACY_APP_ROOT", unset = getwd())
input <- Sys.getenv("ICD10_RAW_CSV", unset = file.path(project_root, "data", "raw", "ICD10_Vietnam.csv"))
if (!file.exists(input)) stop("Không tìm thấy ICD-10 input: ", input)
con <- file(input, "rb")
bom <- readBin(con, "raw", n = 2)
close(con)
encoding <- if (identical(bom, as.raw(c(0xff, 0xfe)))) "UTF-16LE" else if
  (identical(bom, as.raw(c(0xfe, 0xff)))) "UTF-16BE" else "UTF-8"
header <- read_lines(input, n_max = 1, locale = locale(encoding = encoding))
sep <- if (str_detect(header, fixed("\t"))) "\t" else ","
raw <- read_delim(input, delim = sep, locale = locale(encoding = encoding),
                  col_types = cols(.default = col_character()), na = character(), show_col_types = FALSE)
if (!nrow(raw)) stop("File ICD-10 không có dữ liệu.")
blank <- function(x) is.na(x) | str_trim(x) == ""

if (all(c("icd_code", "disease_name_vi", "code_level") %in% names(raw))) {
  code <- raw$icd_code; vi <- raw$disease_name_vi
  en <- if ("disease_name_en" %in% names(raw)) raw$disease_name_en else rep("", nrow(raw))
  level <- raw$code_level
  schema_label <- "Export ICD-10 trước bước chuẩn hóa hiện tại; nguồn đã có cột suy ra"
} else if (all(c("model", "id", "code") %in% names(raw))) {
  code <- raw$code; level <- raw$model
  vi <- if ("name_vi" %in% names(raw)) raw$name_vi else rep("", nrow(raw))
  en <- if ("name_en" %in% names(raw)) raw$name_en else rep("", nrow(raw))
  schema_label <- "Snapshot node cây từ crawler; bao gồm các cấp không phải bệnh"
} else stop("Schema ICD-10 không được nhận diện; giữ nguyên file để kiểm tra.")

out <- Sys.getenv("ICD10_EDA_OUT", unset = file.path(project_root, "reports", "eda", "icd10_raw"))
dir.create(out, recursive = TRUE, showWarnings = FALSE)
qc <- tibble(field = names(raw), blank_records = map_int(raw, ~ sum(blank(.x)))) %>%
  mutate(blank_pct = blank_records / nrow(raw) * 100)
write_csv(qc, file.path(out, "ICD10_raw_missingness.csv"))
identity <- if (all(c("model", "id") %in% names(raw))) paste(raw$model, raw$id, sep = "|") else code
summary <- tibble(metric = c("source_rows", "distinct_source_identity", "exact_duplicate_rows",
  "missing_code", "rows_with_star", "rows_with_dagger"), value = c(nrow(raw),
  n_distinct(identity), nrow(raw) - nrow(distinct(raw)), sum(blank(code)),
  sum(str_detect(coalesce(code, ""), fixed("*"))), sum(str_detect(coalesce(code, ""), fixed("†")))))
write_csv(summary, file.path(out, "ICD10_raw_summary.csv"))
write_csv(tibble(source_row = seq_len(nrow(raw)), code_raw = code, level_raw = level,
  repeated_identity = duplicated(identity) | duplicated(identity, fromLast = TRUE)) %>%
  filter(repeated_identity), file.path(out, "ICD10_raw_identity_review.csv"))

levels <- tibble(level = level) %>% count(level)
write_csv(levels, file.path(out, "ICD10_raw_levels.csv"))
p_level <- ggplot(levels, aes(reorder(level, n), n)) + geom_col(fill = "#307F8C") +
  labs(title = "Số bản ghi theo cấp dữ liệu nguồn", x = "Cấp/node", y = "Bản ghi") + theme_minimal()
p_missing <- ggplot(qc, aes(blank_pct, reorder(field, blank_pct))) + geom_col(fill = "#C27636") +
  labs(title = "Tỷ lệ trường trống trước chuẩn hóa", x = "% trống", y = NULL) + theme_minimal()
lengths <- bind_rows(tibble(language = "VI", value = vi), tibble(language = "EN", value = en)) %>%
  filter(!blank(value)) %>% mutate(characters = nchar(value))
p_length <- ggplot(lengths, aes(characters, fill = language)) +
  geom_histogram(bins = 35, position = "identity", alpha = .5) +
  labs(title = "Độ dài tên nguyên văn", subtitle = "Loại tên trống khỏi phân phối; đã đếm riêng trong bảng chất lượng",
       x = "Số ký tự", y = "Số tên", fill = "Ngôn ngữ") + theme_minimal()
markers <- tibble(marker = case_when(str_detect(coalesce(code, ""), "[*†]") ~ "Có * hoặc †",
  blank(code) ~ "Thiếu mã", TRUE ~ "Không có * hoặc †")) %>% count(marker)
p_marker <- ggplot(markers, aes(marker, n)) + geom_col(fill = "#5874AF") +
  labs(title = "Dấu trong mã gốc được giữ nguyên", x = NULL, y = "Bản ghi") + theme_minimal()
dashboard <- (p_level | p_missing) / (p_length | p_marker) +
  plot_annotation(title = "ICD-10 — EDA trước chuẩn hóa", subtitle = schema_label,
    caption = paste(nrow(raw), "bản ghi nguồn. Số mã không biểu diễn tần suất mắc bệnh."))
ggsave(file.path(out, "ICD10_raw_dashboard.png"), dashboard, width = 17, height = 12, dpi = 180, bg = "white")
cat("Đã lưu EDA ICD-10 trước chuẩn hóa tại:", out, "\n")
