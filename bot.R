library(httr)
library(jsonlite)
library(purrr)
library(rlist)

load("state.rdata")

resp <- GET("https://f1000.com/extapi/work/references?projectId=419191&sort=addedDate:desc", 
    add_headers(Authorization = paste("Bearer", f1000auth)))


if(resp$status_code == 200) {
  refs <- content(resp)$results
  blocks <- list.filter(refs, f1000AddedDate > lastDate) %>% map(function(r){
    details <- paste0(r$authorsText, ". <", r$fullTextLink, "|", r$title, "> ", 
                      r$journalName, ". ", r$publishedYear, 
                      " - added by: ", r$f1000AddedBy,
                      ifelse(length(r$f1000Tags) > 0, 
                             paste0(" - tags: ", paste0(r$f1000Tags, collapse = " ")), 
                             ""))
    
    list(type = "section", text = list(type = "mrkdwn", text = details ))
  })
  content  <- toJSON(list(blocks = blocks), auto_unbox = TRUE)
  outcome <- POST(url = webhook, content_type_json(), body = content)
  if(outcome$status_code == 200){
    lastDate <- refs[[1]]$f1000AddedDate
    save(f1000auth, webhook, lastDate, file = "state.rdata")
  }
}

