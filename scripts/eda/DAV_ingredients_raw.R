# Called by DAV.R. All observations remain at DAV-record grain.
# Raw fields are retained; lexical labels below are exploratory, not chemical IDs.
needed <- c("dav_row_id", "dav_unit", "dav_package", "dav_strength", "dav_dosage_form",
            "dav_price_type", "dav_active_ingredient_raw")
if (!all(needed %in% names(dav_source))) stop("Thiếu cột cho EDA hoạt chất: ",
  paste(setdiff(needed, names(dav_source)), collapse = ", "))

blank <- function(x) is.na(x) | str_trim(x) == ""
lexical <- function(x) str_to_lower(str_squish(coalesce(x, "")))
ingredient_eda <- dav_source %>% mutate(
  .row = row_number(),
  ingredient_raw = coalesce(dav_active_ingredient_raw, ""),
  ingredient_lexical = lexical(ingredient_raw),
  missing_ingredient = blank(ingredient_raw),
  has_semicolon = str_detect(ingredient_raw, fixed(";")),
  has_plus = str_detect(ingredient_raw, fixed("+")),
  has_slash = str_detect(ingredient_raw, fixed("/")),
  has_salt_note = str_detect(str_to_lower(ingredient_raw), "dưới\\s+dạng"),
  has_equivalence = str_detect(str_to_lower(ingredient_raw), "tương\\s*(đương|ứng)"),
  has_embedded_strength = str_detect(ingredient_raw,
    regex("[0-9]\\s*(mg|mcg|ug|µg|μg|g|ml|iu|ui|%)", ignore_case = TRUE)),
  price_vnd = dav$dav_price_vnd,
  receipt_month = format(dav$declaration_date, "%Y-%m")
)
write_csv(ingredient_eda %>% select(.row, dav_row_id, ingredient_raw, ingredient_lexical,
  missing_ingredient, starts_with("has_")), file.path(output_dir, "DAV_raw_ingredient_diagnostics.csv"))

flags <- ingredient_eda %>% summarise(across(c(missing_ingredient, starts_with("has_")), sum)) %>%
  pivot_longer(everything(), names_to = "pattern", values_to = "records")
flags$label <- c("Thiếu hoạt chất", "Có dấu ;", "Có dấu +", "Có dấu /",
                 "Có 'dưới dạng'", "Có tương đương/tương ứng", "Có số và đơn vị trong hoạt chất")
write_csv(flags, file.path(output_dir, "DAV_raw_ingredient_patterns.csv"))
p_patterns <- ggplot(flags, aes(records, reorder(label, records))) +
  geom_col(fill = "#307F8C") + labs(title = "Những chuỗi cần rà soát trước khi tách hoạt chất",
    subtitle = "Các nhóm có thể chồng lấp; dấu + hoặc / chưa chứng minh là đa hoạt chất",
    x = "Số bản ghi", y = NULL) + theme_minimal()

frequency <- ingredient_eda %>% filter(!missing_ingredient) %>%
  count(ingredient_raw, sort = TRUE)
write_csv(frequency, file.path(output_dir, "DAV_raw_ingredient_frequency.csv"))
p_frequency <- frequency %>% slice_head(n = 12) %>%
  mutate(label = str_trunc(ingredient_raw, 55)) %>%
  ggplot(aes(n, reorder(label, n))) + geom_col(fill = "#5874AF") +
  labs(title = "Các chuỗi hoạt chất nguyên văn xuất hiện nhiều",
       subtitle = "Số bản ghi kê khai; không phải doanh số hay số phân tử khác nhau", x = "Số bản ghi", y = NULL) + theme_minimal()

variants <- ingredient_eda %>% filter(!missing_ingredient) %>%
  distinct(ingredient_lexical, ingredient_raw) %>% group_by(ingredient_lexical) %>%
  summarise(raw_spellings = n(), examples = paste(head(ingredient_raw, 4), collapse = " | "), .groups = "drop") %>%
  arrange(desc(raw_spellings))
write_csv(variants, file.path(output_dir, "DAV_raw_lexical_variants.csv"))
p_variants <- variants %>% count(raw_spellings) %>%
  ggplot(aes(factor(raw_spellings), n)) + geom_col(fill = "#8B6C97") +
  labs(title = "Biến thể do chữ hoa/thường và khoảng trắng",
       subtitle = "Chỉ tạo nhãn khảo sát; chưa gộp muối, tên đồng nghĩa hay hàm lượng",
       x = "Số cách viết raw trong một nhãn", y = "Số nhãn khảo sát") + theme_minimal()

# Use full ingredient expression for a mixture: never duplicate its price for
# every component or claim that the price of the mixture is the price of each drug.
contexts <- c("ingredient_lexical", "dav_strength", "dav_dosage_form", "dav_unit",
              "dav_package", "dav_price_type", "receipt_month")
candidate_rows <- ingredient_eda %>% filter(
  is.finite(price_vnd), price_vnd > 0, !blank(dav_row_id),
  if_all(all_of(contexts), ~ !blank(.x)))
context_summary <- candidate_rows %>% group_by(across(all_of(contexts))) %>%
  summarise(records = n(), distinct_dav_ids = n_distinct(dav_row_id),
    median_vnd = median(price_vnd), q1_vnd = quantile(price_vnd, .25),
    q3_vnd = quantile(price_vnd, .75), .groups = "drop") %>%
  arrange(desc(distinct_dav_ids), desc(records)) %>% mutate(context_id = row_number())
write_csv(context_summary, file.path(output_dir, "DAV_raw_price_contexts.csv"))
write_csv(tibble(metric = c("input_records", "positive_price_complete_context_records",
  "excluded_records", "context_groups_with_at_least_5_DAV_IDs"), value = c(nrow(ingredient_eda),
  nrow(candidate_rows), nrow(ingredient_eda) - nrow(candidate_rows),
  sum(context_summary$distinct_dav_ids >= 5))), file.path(output_dir, "DAV_raw_price_context_coverage.csv"))

shown <- context_summary %>% filter(distinct_dav_ids >= 5) %>% slice_head(n = 8) %>%
  mutate(label = paste0("#", context_id, " ", str_trunc(ingredient_lexical, 26),
    " | ", str_trunc(dav_strength, 14), " | ", dav_unit, " | n=", distinct_dav_ids))
if (nrow(shown)) {
  p_price <- ggplot(shown, aes(median_vnd, reorder(label, median_vnd))) +
    geom_segment(aes(x = q1_vnd, xend = q3_vnd, yend = reorder(label, median_vnd)), colour = "#C27636") +
    geom_point(colour = "#C27636") + scale_x_log10(labels = scales::label_number()) +
    labs(title = "Giá trong các nhóm cùng thông tin raw", x = "VND theo đơn vị ghi trong từng nhóm (trục log10)", y = NULL,
      subtitle = "Chấm: median; đoạn: Q1–Q3. Xem CSV để biết dạng, quy cách, loại giá và tháng") + theme_minimal()
} else {
  p_price <- ggplot() + annotate("text", x = 0, y = 0,
    label = "Chưa có nhóm đủ thông tin và ít nhất 5 DAV ID") + theme_void()
}
ingredient_dashboard <- (p_patterns | p_frequency) / (p_variants | p_price) +
  plot_annotation(title = "DAV — khảo sát hoạt chất và giá trước chuẩn hóa",
    caption = "Khác biệt giá giữa các nhóm không xác định tác động nhân quả của hoạt chất. Nhà sản xuất, thời gian và cấu trúc sản phẩm vẫn cần kiểm soát.")
ggsave(file.path(output_dir, "DAV_raw_ingredient_dashboard.png"), ingredient_dashboard,
       width = 20, height = 13, dpi = 180, bg = "white")
