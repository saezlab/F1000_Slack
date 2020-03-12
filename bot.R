suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(purrr)
  library(dplyr)
  library(rlist)
})

load("/path/to/state.rdata")

templastDate <- lastDate

webhooks %>% pwalk(function(...) {
  current <- data.frame(...)
  resp <- GET(
    paste0("https://f1000.com/extapi/work/references?projectId=", current$projectId, "&sort=addedDate:desc"),
    add_headers(Authorization = paste("Bearer", f1000auth))
  )
  print(paste(Sys.time(), "channel", current$channel, "GET status", resp$status_code))

  if (resp$status_code == 200) {
    refs <- content(resp)$results
    blocks <- list.filter(refs, f1000AddedDate > lastDate) %>% map(function(r) {
      details <- paste0(
        r$authorsText, ". <", r$fullTextLink, "|", r$title, "> ",
        r$journalName, ". ", r$publishedYear,
        " - added by: ", r$f1000AddedBy,
        ifelse(length(r$f1000Tags) > 0,
          paste0(" - tags: ", paste0(r$f1000Tags, collapse = " ")),
          ""
        )
      )

      list(type = "section", text = list(type = "mrkdwn", text = details))
    })

    if (length(blocks) > 0) {
      webhook <- as.character(current$webhook)

      content <- toJSON(list(blocks = blocks), auto_unbox = TRUE)
      outcome <- POST(url = webhook, content_type_json(), body = content)

      print(paste(Sys.time(), "POST status", outcome$status_code))

      if (outcome$status_code == 200) {
        if (refs[[1]]$f1000AddedDate > templastDate) {
          templastDate <- refs[[1]]$f1000AddedDate
        }
      }
    }
  }
})

if(templastDate > lastDate){
  save(f1000auth, webhooks, templastDate, file = "/path/to/state.rdata")
}



