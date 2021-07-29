suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(purrr)
  library(dplyr)
})

get_page <- function(projectId, page, f1000auth) {
  resp <- GET(
    paste0("https://sciwheel.com/extapi/work/references?projectId=", projectId, "&sort=addedDate:desc&page=", page),
    add_headers(Authorization = paste("Bearer", f1000auth))
  )

  if (resp$status_code == 200) {
    project.content <- content(resp)

    project.notes <- project.content$results %>% map(function(r) {
      GET(
        paste0("https://sciwheel.com/extapi/work/references/", r$id, "/notes?"),
        add_headers(Authorization = paste("Bearer", f1000auth))
      ) %>% content()
    })

    names(project.notes) <- project.content$results %>% map_chr(~ .x$id)
    list(content = project.content, notes = project.notes)
  } else {
    NULL
  }
}

get_pages <- function(projectId, f1000auth, page = 1) {
  page.results <- get_page(projectId, page, f1000auth)

  if (page >= page.results$content$totalPages) {
    list(content = page.results$content$results, notes = page.results$notes)
  } else {
    next.page.results <- get_pages(projectId, f1000auth, page + 1)
    list(
      content = c(page.results$content$results, next.page.results$content),
      notes = c(page.results$notes, next.page.results$notes)
    )
  }
}

load("state.rdata")

distinct.webhooks <- webhooks %>% distinct(projectId, .keep_all = TRUE)

dump <- distinct.webhooks %>% pmap(function(...) {
  current <- data.frame(...)
  get_pages(current$projectId, f1000auth)
})

names(dump) <- distinct.webhooks$channel

if (file.exists("data_dump.rds")) {
  file.rename("data_dump.rds", "data_dump_old.rds")
}

saveRDS(dump, "data_dump.rds")
