nested_list <- readRDS("data_dump.rds")

# Extracting "f1000AddedBy" field
extract_added_by <- function(list_item) {
  sapply(list_item$content, function(content_item) content_item$f1000AddedBy)
}

# Flatten the list of "f1000AddedBy" values
added_by_list <- unlist(lapply(nested_list, extract_added_by), use.names = FALSE)

# Count occurrences of each lab member
library(dplyr)
added_by_count <- as.data.frame(table(added_by_list)) %>%
  rename(Member = added_by_list, Count = Freq)

# Plot the data
library(ggplot2)
ggplot(added_by_count, aes(x = Member, y = Count)) +
  geom_bar(stat = "identity") +
  labs(title = "Number of Papers Added by Each Lab Member",
       x = "Lab Member",
       y = "Number of Papers") +
  theme_minimal()


# Sort the data in descending order of counts
added_by_count <- added_by_count %>%
  arrange(desc(Count))

# Plot the data with horizontal bars and sorted order
ggplot(added_by_count, aes(x = reorder(Member, Count), y = Count)) +
  geom_bar(stat = "identity") +
  labs(title = "Number of Papers Added by Each Lab Member",
       x = "Lab Member",
       y = "Number of Papers") +
  theme_minimal() +
  coord_flip()


#####

# Extracting "f1000AddedBy" and "f1000AddedDate" fields robustly
extract_added_info <- function(list_item) {
  if (is.null(list_item$content)) return(data.frame(f1000AddedBy = NA, f1000AddedDate = NA))
  do.call(rbind, lapply(list_item$content, function(content_item) {
    data.frame(
      f1000AddedBy = ifelse(is.null(content_item$f1000AddedBy), NA, content_item$f1000AddedBy),
      f1000AddedDate = ifelse(is.null(content_item$f1000AddedDate), NA, content_item$f1000AddedDate)
    )
  }))
}

# Flatten and transform the nested list into a single data frame
added_info <- do.call(rbind, lapply(nested_list, extract_added_info))

# Convert dates from millis to R date format and filter by year 2024
added_info <- added_info %>%
  mutate(f1000AddedDate = as.POSIXct(as.numeric(f1000AddedDate) / 1000, origin = "1970-01-01"),
         Year = format(f1000AddedDate, "%Y")) %>%
  filter(Year == "2024" & !is.na(f1000AddedBy))

# Count occurrences of each lab member
added_by_count <- added_info %>%
  count(f1000AddedBy, name = "Count") %>%
  rename(Member = f1000AddedBy)

# Plot the data with horizontal bars and sorted order
ggplot(added_by_count, aes(x = reorder(Member, Count), y = Count)) +
  geom_bar(stat = "identity") +
  labs(title = "Number of Papers Added by Each Lab Member in 2024",
       x = "Lab Member",
       y = "Number of Papers") +
  theme_minimal() +
  coord_flip()
