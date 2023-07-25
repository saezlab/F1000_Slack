suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(purrr)
  library(dplyr)
  library(googledrive)
  library(readr)
})

# get command line secret inputs
args <- commandArgs(trailingOnly = TRUE)

# 1st: credentials for Google Drive
credentials <- args[[1]]

# Dump file path on google drive: 
file_path <- args[[2]]


# Sciwheel authentication token:
f1000auth <- args[[3]]


# Load the state from Drive
# authenticate to Google drive
drive_auth(path = rawToChar(base64enc::base64decode(credentials)), email = "f1000bot-service-account@f1000bot.iam.gserviceaccount.com" )

# read the file from google drive
webhooks <- drive_read_string(file = file_path) %>% read_csv()



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



distinct.webhooks <- webhooks %>% distinct(projectId, .keep_all = TRUE)

dump <- distinct.webhooks %>% pmap(function(...) {
  current <- data.frame(...)
  get_pages(current$projectId, f1000auth)
})

names(dump) <- distinct.webhooks$channel


saveRDS(dump, "./data_dump.rds")
drive_update(file = file_path, media = "./data_dump.rds")

