library(tidyverse)
library(patchwork)

# Mở RStudio tại thư mục gốc Pharmacy_app. Có thể đặt PHARMACY_APP_ROOT
# nếu chạy script từ một thư mục khác.
configured_root <- Sys.getenv("PHARMACY_APP_ROOT", unset = "")
if (nzchar(configured_root)) {
  if (!dir.exists(configured_root)) stop("PHARMACY_APP_ROOT không tồn tại: ", configured_root)
  project_root <- normalizePath(configured_root, winslash = "/", mustWork = TRUE)
} else {
  candidates <- unique(normalizePath(
    c(getwd(), file.path(getwd(), ".."), file.path(getwd(), "../..")),
    winslash = "/", mustWork = FALSE
  ))
  matches <- candidates[file.exists(file.path(candidates, "data", "raw", "DAVfull.csv"))]
  project_root <- if (length(matches)) matches[[1]] else getwd()
}
default_dav <- file.path(project_root, "data", "raw", "DAVfull.csv")
csv_file <- Sys.getenv("DAV_RAW_CSV", unset = default_dav)
if (!file.exists(csv_file)) stop("Không tìm thấy DAV input: ", csv_file)

# Pharmacy_app/data/raw/DAVfull.csv là UTF-16LE, phân cách bằng tab.
# File do crawler tạo là UTF-8, phân cách bằng dấu phẩy. Tự nhận dạng hai dạng này.
con <- file(csv_file, open = "rb")
bom <- readBin(con, what = "raw", n = 4)
close(con)
is_utf16le <- length(bom) >= 2 && identical(bom[1:2], as.raw(c(0xFF, 0xFE)))
is_utf16be <- length(bom) >= 2 && identical(bom[1:2], as.raw(c(0xFE, 0xFF)))
delimiter <- if (is_utf16le || is_utf16be) "\t" else ","
encoding <- if (is_utf16le) "UTF-16LE" else if (is_utf16be) "UTF-16BE" else "UTF-8"

dav <- readr::read_delim(
  csv_file,
  delim = delimiter,
  locale = readr::locale(encoding = encoding),
  na = c("", "NA", "#N/A"),
  col_types = readr::cols(
    dav_declaration_date = readr::col_character(),
    .default = readr::col_character()
  ),
  show_col_types = FALSE,
  progress = FALSE
)

required_columns <- c(
  "dav_declaration_date", "dav_price_vnd", "dav_price_type",
  "dav_strength", "dav_dosage_form", "dav_country", "dav_package",
  "dav_active_ingredient_raw"
)
missing_columns <- setdiff(required_columns, names(dav))
if (length(missing_columns) > 0) {
  stop(
    "File DAV thiếu cột cần cho biểu đồ: ",
    paste(missing_columns, collapse = ", "),
    ". Hãy chọn file DAV do crawler tạo hoặc DAVfull.csv trong data/raw."
  )
}
cat("Đã đọc", nrow(dav), "dòng từ:", normalizePath(csv_file), "\n")

# Chuẩn bị biến cần vẽ
dav_source <- dav  # Giữ nguyên văn bản nguồn cho EDA trước chuẩn hóa.
dav <- dav %>%
  mutate(
    dav_price_vnd = as.numeric(dav_price_vnd),
    declaration_date = as.Date(
      substr(dav_declaration_date, 1, 10),
      format = "%Y-%m-%d"
    ),
    year = as.integer(format(declaration_date, "%Y")),
    log_price = if_else(
      !is.na(dav_price_vnd) & dav_price_vnd >= 0,
      log1p(pmax(dav_price_vnd, 0)),
      NA_real_
    )
  )

# Thứ tự nhóm giá theo số dòng
type_order <- dav %>%
  count(dav_price_type, sort = TRUE) %>%
  pull(dav_price_type)

# ---------------------------------------------------------
# Biểu đồ 1: Histogram giá sau log1p
# ---------------------------------------------------------

q_data <- tibble(
  x = as.numeric(
    quantile(
      dav$log_price,
      probs = c(0.25, 0.50, 0.75, 0.95),
      na.rm = TRUE
    )
  ),
  mốc = factor(
    c("Q1", "Median", "Q3", "P95"),
    levels = c("Q1", "Median", "Q3", "P95")
  )
)

p_hist <- ggplot(
  dav %>% filter(!is.na(log_price)),
  aes(x = log_price)
) +
  geom_histogram(
    bins = 48,
    fill = "#3F73A8",
    colour = "white"
  ) +
  geom_vline(
    data = q_data,
    aes(xintercept = x, linetype = mốc),
    colour = "#165D9C",
    linewidth = 0.8,
    inherit.aes = FALSE
  ) +
  scale_linetype_manual(
    values = c(
      "Q1" = "dashed",
      "Median" = "dashed",
      "Q3" = "dashed",
      "P95" = "dashed"
    ),
    name = "Mốc"
  ) +
  labs(
    title = "Giá: histogram sau log1p",
    x = "ln(giá VND + 1)",
    y = "Số bản ghi"
  ) +
  theme_minimal(base_size = 11)

# ---------------------------------------------------------
# Biểu đồ 2: Boxplot giá theo loại giá
# ---------------------------------------------------------

p_box <- ggplot(
  dav %>% filter(!is.na(log_price), !is.na(dav_price_type)),
  aes(
    x = log_price,
    y = reorder(dav_price_type, log_price, FUN = median)
  )
) +
  geom_boxplot(
    outlier.shape = NA,
    fill = "#F0A34A",
    colour = "#333333"
  ) +
  labs(
    title = "Boxplot theo loại giá",
    subtitle = "Các chấm ngoại lệ được ẩn để hộp dễ đọc",
    x = "ln(giá VND + 1)",
    y = "Loại giá"
  ) +
  theme_minimal(base_size = 11)

# ---------------------------------------------------------
# Biểu đồ 3: Số dòng và median giá theo năm
# ---------------------------------------------------------

yearly <- dav %>%
  filter(!is.na(year), !is.na(dav_price_vnd), dav_price_vnd >= 0) %>%
  group_by(year) %>%
  summarise(
    rows = n(),
    median_price = median(dav_price_vnd),
    .groups = "drop"
  )

# Quy đổi tạm median lên cùng vùng hiển thị với số dòng.
# Trục bên phải sẽ quy đổi ngược để hiển thị median bằng VND.
if (!nrow(yearly)) stop("Không có ngày và giá hợp lệ để vẽ chuỗi thời gian.")
scale_factor <- if (max(yearly$median_price) > 0) max(yearly$rows) / max(yearly$median_price) else 1

p_year <- ggplot(yearly, aes(x = year)) +
  geom_col(
    aes(y = rows),
    fill = "#88B6A1",
    alpha = 0.8
  ) +
  geom_line(
    aes(y = median_price * scale_factor),
    colour = "#B14D4B",
    linewidth = 1
  ) +
  geom_point(
    aes(y = median_price * scale_factor),
    colour = "#B14D4B",
    size = 2
  ) +
  scale_y_continuous(
    name = "Số bản ghi",
    sec.axis = sec_axis(
      ~ . / scale_factor,
      name = "Trung vị giá (VND)"
    )
  ) +
  scale_x_continuous(
    breaks = seq(min(yearly$year), max(yearly$year), by = 2)
  ) +
  labs(
    title = "Theo năm tiếp nhận ghi trong nguồn",
    subtitle = paste("Ngày tiếp nhận lớn nhất trong file:", max(dav$declaration_date, na.rm = TRUE),
                     "— trung vị gộp mô tả snapshot, có thể đổi do cơ cấu bản ghi"),
    x = "Năm"
  ) +
  theme_minimal(base_size = 11)

# ---------------------------------------------------------
# Biểu đồ 4: Heatmap tỷ lệ thiếu theo loại giá
# ---------------------------------------------------------

missing_cols <- c(
  "dav_strength",
  "dav_dosage_form",
  "dav_country",
  "dav_package",
  "dav_active_ingredient_raw"
)

field_labels <- c(
  dav_strength = "Hàm lượng",
  dav_dosage_form = "Dạng bào chế",
  dav_country = "Quốc gia",
  dav_package = "Quy cách",
  dav_active_ingredient_raw = "Hoạt chất"
)

missing_by_type <- dav %>%
  group_by(dav_price_type) %>%
  summarise(
    across(
      all_of(missing_cols),
      ~ mean(is.na(.x)) * 100
    ),
    .groups = "drop"
  ) %>%
  pivot_longer(
    cols = all_of(missing_cols),
    names_to = "field",
    values_to = "missing_pct"
  ) %>%
  mutate(
    field = factor(
      field,
      levels = missing_cols,
      labels = unname(field_labels[missing_cols])
    ),
    dav_price_type = factor(
      dav_price_type,
      levels = rev(type_order)
    )
  )

p_missing <- ggplot(
  missing_by_type,
  aes(x = field, y = dav_price_type, fill = missing_pct)
) +
  geom_tile(colour = "white") +
  geom_text(
    aes(label = sprintf("%.0f", missing_pct)),
    size = 3
  ) +
  scale_fill_gradient(
    low = "#FFF7BC",
    high = "#B2182B",
    limits = c(0, 100),
    name = "% thiếu"
  ) +
  labs(
    title = "Thiếu dữ liệu theo loại giá",
    subtitle = "Mỗi ô là phần trăm thiếu trong nhóm đó",
    x = "Trường dữ liệu",
    y = "Loại giá"
  ) +
  theme_minimal(base_size = 11) +
  theme(
    axis.text.x = element_text(angle = 30, hjust = 1)
  )

# ---------------------------------------------------------
# Ghép bốn biểu đồ và lưu ảnh
# ---------------------------------------------------------

dashboard <- (p_hist | p_box) / (p_year | p_missing) +
  plot_annotation(
    title = "DAV: phân phối, nhóm giá, thời gian và dữ liệu thiếu"
  )

dashboard

output_dir <- file.path(project_root, "reports", "eda", "dav")
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
ggsave(
  filename = "DAV_R_dashboard.png",
  plot = dashboard,
  path = output_dir,
  width = 16,
  height = 11,
  dpi = 200
)

# Hoạt chất trước chuẩn hóa: giữ raw, chỉ tạo biến mô tả và bảng rà soát.
source(file.path(project_root, "scripts", "eda", "DAV_ingredients_raw.R"), local = TRUE)
