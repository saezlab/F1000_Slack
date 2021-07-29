library(dplyr)
library(purrr)
library(ggplot2)
library(stringr)

dump <- readRDS("data_dump.rds")

tags <- unlist(dump %>%
                 map(~ .x$content %>%
                       map(~ .x$f1000Tags)))

tag.stats <- tibble(channel = str_remove(names(tags),"\\d+"), tag = tags)
  
  
total.tag.counts <- tag.stats %>% group_by(tag) %>% 
  summarize(count = n())

ggplot(total.tag.counts, aes(x = reorder(tag, -count), y = count)) + 
  geom_point() +
  scale_y_log10() +
  xlab("Tag") +
  ylab("Count") +
  theme_classic() +
  theme(axis.text.x = element_text(angle = 90, hjust = 1))

channel.tag.counts <- tag.stats %>% group_by(channel, tag) %>% 
  summarize(count = n())

ggplot(channel.tag.counts, aes(x = tag, y = count, fill = channel)) + 
  geom_bar(position="stack", stat="identity") +
  xlab("Tag") +
  ylab("Count") +
  scale_fill_brewer(palette = "Set3") +
  theme_classic() +
  theme(axis.text.x = element_text(angle = 90, hjust = 1))



